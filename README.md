# Back In Time To Be Rich — data

Static market data for the *Back In Time To Be Rich* iOS app. The app downloads
`data_manifest.json` plus the files it lists and computes trades on device. A GitHub
Action refreshes the data daily (22:30 UTC) and commits it; the app reads `docs/` straight
from GitHub's raw file host, with jsDelivr as a mirror.

## Base URLs

- Primary: `https://raw.githubusercontent.com/yifan024/BackInTimeToBeRich-data/main/docs/` (5-minute cache, ETag)
- Mirror: `https://cdn.jsdelivr.net/gh/yifan024/BackInTimeToBeRich-data@main/docs/` (CDN, may lag up to ~12 h)

GitHub Pages is not used: this account's user site has a custom domain, which redirects
project Pages sites away from GitHub.

## Layout

```
docs/data_manifest.json        manifest (schemaVersion 3)
docs/prices/<ID>.hist.csv      daily closes before Jan 1 of the current year
docs/prices/<ID>.<year>.csv    daily closes of the current year
docs/lottery/*.csv             lottery draw history
pipeline/assets.json           asset universe (source of truth)
pipeline/update_prices.py      daily updater
pipeline/validate.py           integrity checks
.github/workflows/update-data.yml
```

## Format

Price CSVs: header `date,close`, ISO dates ascending, `close` as a plain decimal with up
to 6 significant digits, UTF-8, `\n` line endings. The manifest lists every asset
(`id`, `category`, `region`, `name`, `currency`, `shortable`, `files[]` with `path`,
`sha256`, `bytes`), the lottery files, `dataThrough` and `generatedAt`. Full contract:
section 2 of `docs/LOCAL_COMPUTE_DESIGN.md` in the app repo.

The manifest is only rewritten when a file hash or `dataThrough` changes, so its HTTP
ETag stays stable on days with no new data.

## Add or remove an asset

Edit `pipeline/assets.json` (`id`, `yahoo` symbol, `category` stocks|crypto, `region`
us|cn, `name`, `currency`, `shortable`), then run the updater (locally or via the
workflow). New assets are fetched in full; removed assets and their files are dropped
from the manifest and `docs/prices`.

## Run locally

```
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r pipeline/requirements.txt
python pipeline/update_prices.py --root docs            # add --dry-run, --only AAPL,BTC
python pipeline/validate.py --root docs
```

Safety: if a fetch fails, returns a shorter/older series, loses over 5% of rows or has a
non-positive close, that asset's existing files are kept. The updater exits non-zero only
if every asset failed.

## Data source

Daily closes from Yahoo Finance via `yfinance` (split-adjusted, not dividend-adjusted;
dates are exchange-local). For informational use only; no guarantee of accuracy.
