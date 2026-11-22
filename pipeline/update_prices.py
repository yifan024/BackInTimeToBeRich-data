#!/usr/bin/env python3
"""Daily price updater for the Back In Time To Be Rich data repo.

Fetches daily closes from Yahoo Finance (yfinance), merges them into the published
series under <root>/prices, and rebuilds <root>/data_manifest.json.

    python pipeline/update_prices.py --root docs [--today YYYY-MM-DD] [--dry-run] [--only AAPL,BTC]

Exit code is non-zero only if ALL assets failed (or kept their old data after errors).
"""
import argparse
import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

LOTTERY = [("powerball", "us"), ("megamillions", "us"), ("shuangseqiu", "cn")]
SCHEMA_VERSION = 3
MIN_ROW_RATIO = 0.95
RETRIES = 3


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def fmt_close(x: float) -> str:
    """Up to 6 significant digits, plain decimal, no trailing zeros."""
    s = format(Decimal(format(x, ".6g")), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def read_csv_rows(path: Path):
    rows = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[r["date"]] = r["close"]
    return rows


def existing_series(root: Path, entry):
    """Existing published rows for an asset, from the manifest's file list (later wins)."""
    rows = {}
    if not entry:
        return []
    for fe in entry["files"]:
        p = root / fe["path"]
        if p.exists():
            rows.update(read_csv_rows(p))
    return sorted(rows.items())


def fetch_history(yahoo: str):
    import yfinance as yf
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            df = yf.Ticker(yahoo).history(period="max", interval="1d", auto_adjust=False)
            if df is None or df.empty:
                raise RuntimeError("empty result")
            rows = {}
            for idx, v in df["Close"].items():
                v = float(v)
                if v != v:  # NaN
                    continue
                d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
                rows[d] = v
            if not rows:
                raise RuntimeError("no valid closes")
            return sorted(rows.items())
        except Exception as e:  # noqa: BLE001 - any fetch failure is retried
            last = e
            if attempt < RETRIES:
                time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"fetch failed after {RETRIES} attempts: {last}")


def merge(existing, fetched):
    """Keep existing rows before the fetched first date; fetched rows win."""
    first = fetched[0][0]
    merged = [(d, c) for d, c in existing if d < first]
    merged.extend(fetched)
    return merged


def check_safety(existing, fetched_raw, merged):
    if any(v <= 0 for _, v in fetched_raw):
        return "non-positive close in fetched data"
    if existing:
        if fetched_raw[-1][0] < existing[-1][0]:
            return f"fetched last date {fetched_raw[-1][0]} < existing {existing[-1][0]}"
        if len(merged) < MIN_ROW_RATIO * len(existing):
            return f"merged rows {len(merged)} < 95% of existing {len(existing)}"
    return None


def csv_bytes(rows):
    return ("date,close\n" + "".join(f"{d},{c}\n" for d, c in rows)).encode("utf-8")


def file_entry(rel: str, data: bytes):
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="docs", type=Path)
    ap.add_argument("--assets", default="pipeline/assets.json", type=Path)
    ap.add_argument("--today", help="YYYY-MM-DD (default: UTC today)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="comma-separated asset ids")
    a = ap.parse_args()

    today = a.today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    year = int(today[:4])
    cutoff = f"{year}-01-01"
    root: Path = a.root

    assets = json.loads(a.assets.read_text(encoding="utf-8"))["assets"]
    only = set(a.only.split(",")) if a.only else None
    mpath = root / "data_manifest.json"
    old_manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else None
    old_entries = {e["id"]: e for e in (old_manifest or {}).get("assets", [])}

    writes = {}      # rel path -> bytes (changed or new files only)
    entries = []     # new manifest asset entries
    summary = []
    failed = ok = 0
    through = ""

    for asset in assets:
        aid = asset["id"]
        old = old_entries.get(aid)
        if only and aid not in only:
            # untouched: carry over existing entry
            if old:
                entries.append(old)
                through = max(through, _last_date(root, old))
            continue
        existing = existing_series(root, old)
        status, note, rows = "ok", "", None
        try:
            fetched = fetch_history(asset["yahoo"])
            merged = merge(existing, fetched)
            reason = check_safety(existing, fetched, merged)
            if reason:
                raise RuntimeError(reason)
            rows = [(d, fmt_close(v) if isinstance(v, float) else v) for d, v in merged]
        except Exception as e:  # noqa: BLE001
            status, note = "KEPT", str(e)
            log(f"WARNING {aid} ({asset['yahoo']}): {e} -- keeping existing files")
        if rows is None:
            failed += 1
            if old:
                entries.append(old)
                through = max(through, _last_date(root, old))
                summary.append((aid, len(existing), existing[-1][0] if existing else "-", status, note))
            else:
                summary.append((aid, 0, "-", status, note))
            continue
        ok += 1
        files, changed = [], []
        parts = (("hist", [r for r in rows if r[0] < cutoff]),
                 (str(year), [r for r in rows if r[0] >= cutoff]))
        for suffix, part in parts:
            if not part:
                continue
            rel = f"prices/{aid}.{suffix}.csv"
            data = csv_bytes(part)
            files.append(file_entry(rel, data))
            p = root / rel
            if not p.exists() or p.read_bytes() != data:
                writes[rel] = data
                changed.append(suffix)
        through = max(through, rows[-1][0])
        entries.append({"id": aid, "category": asset["category"], "region": asset["region"],
                        "name": asset["name"], "currency": asset["currency"],
                        "shortable": asset["shortable"], "files": files})
        summary.append((aid, len(rows), rows[-1][0], "changed:" + ",".join(changed) if changed else "unchanged", ""))

    entries.sort(key=lambda x: (x["category"], x["region"], x["id"]))

    lottery = []
    old_lot = {e["id"]: e for e in (old_manifest or {}).get("lottery", [])}
    for n, region in LOTTERY:
        rel = f"lottery/{n}.csv"
        p = root / rel
        if not p.exists():
            log(f"WARNING lottery file missing: {rel}")
            continue
        lottery.append({"id": n, "region": old_lot.get(n, {}).get("region", region),
                        "file": file_entry(rel, p.read_bytes())})

    new_manifest = {"schemaVersion": SCHEMA_VERSION, "generatedAt": "", "dataThrough": through,
                    "assets": entries, "lottery": lottery}
    manifest_changed = True
    if old_manifest:
        a1 = dict(old_manifest, generatedAt="")
        manifest_changed = json.dumps(a1, sort_keys=False) != json.dumps(new_manifest, sort_keys=False)
    referenced = {f["path"] for e in entries for f in e["files"]}
    stale = sorted(str(p.relative_to(root)) for p in (root / "prices").glob("*") if p.is_file()
                   and str(p.relative_to(root)) not in referenced) if (root / "prices").exists() else []

    print(f"{'asset':<12} {'rows':>6} {'last date':<11} status")
    for aid, n, last, st, note in summary:
        print(f"{aid:<12} {n:>6} {last:<11} {st}{(' -- ' + note) if note else ''}")
    print(f"\nassets ok={ok} kept/failed={failed} files written={len(writes)} stale removed={len(stale)} "
          f"manifest {'rewritten' if manifest_changed else 'unchanged'} dataThrough={through}")

    if not a.dry_run:
        for rel, data in writes.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        for rel in stale:
            (root / rel).unlink()
        if manifest_changed:
            new_manifest["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            mpath.write_text(json.dumps(new_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if ok == 0 and failed > 0:
        log("ERROR: all assets failed")
        return 1
    return 0


def _last_date(root, entry):
    rows = existing_series(root, entry)
    return rows[-1][0] if rows else ""


if __name__ == "__main__":
    sys.exit(main())
