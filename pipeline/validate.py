#!/usr/bin/env python3
"""Validate a published data folder (stdlib only). Exit 1 on any problem."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_file(root, fe, problems, prices):
    p = root / fe["path"]
    if not p.is_file():
        problems.append(f"missing file: {fe['path']}")
        return
    data = p.read_bytes()
    if len(data) != fe["bytes"]:
        problems.append(f"{fe['path']}: bytes {len(data)} != manifest {fe['bytes']}")
    if hashlib.sha256(data).hexdigest() != fe["sha256"]:
        problems.append(f"{fe['path']}: sha256 mismatch")
    if not prices:
        return
    try:
        lines = data.decode("utf-8").split("\n")
    except UnicodeDecodeError:
        problems.append(f"{fe['path']}: not UTF-8")
        return
    if lines[0] != "date,close":
        problems.append(f"{fe['path']}: bad header {lines[0]!r}")
    if lines[-1] != "":
        problems.append(f"{fe['path']}: missing trailing newline")
    prev = ""
    for i, line in enumerate(lines[1:-1], start=2):
        parts = line.split(",")
        if len(parts) != 2 or not DATE.match(parts[0]):
            problems.append(f"{fe['path']}:{i}: malformed row {line!r}")
            return
        if parts[0] <= prev:
            problems.append(f"{fe['path']}:{i}: date {parts[0]} not ascending after {prev}")
            return
        prev = parts[0]
        try:
            if not float(parts[1]) > 0:
                raise ValueError
        except ValueError:
            problems.append(f"{fe['path']}:{i}: non-positive/invalid close {parts[1]!r}")
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="docs", type=Path)
    a = ap.parse_args()
    root, problems = a.root, []
    try:
        m = json.loads((root / "data_manifest.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: cannot read manifest: {e}")
        return 1
    if m.get("schemaVersion") != 3:
        problems.append(f"schemaVersion {m.get('schemaVersion')} != 3")
    referenced = set()
    for asset in m.get("assets", []):
        if not asset.get("files"):
            problems.append(f"{asset['id']}: no files")
        for fe in asset.get("files", []):
            referenced.add(fe["path"])
            check_file(root, fe, problems, True)
    for lot in m.get("lottery", []):
        referenced.add(lot["file"]["path"])
        check_file(root, lot["file"], problems, False)
    pd = root / "prices"
    if pd.exists():
        for p in sorted(pd.iterdir()):
            rel = f"prices/{p.name}"
            if rel not in referenced:
                problems.append(f"unreferenced file: {rel}")
    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for p in problems:
            print(" -", p)
        return 1
    print(f"OK: {len(m['assets'])} assets, {len(m.get('lottery', []))} lottery files, dataThrough {m['dataThrough']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
