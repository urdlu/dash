# Tim's Desk

Personal dashboard + automation. See `CLAUDE.md` for full context, constraints and the phased plan.

## Phase 1 setup (one-time)

### 1. Google Cloud service account

Using the existing **WS Trades** project (`solid-linker-505403-j8`, in `wstradesws@gmail.com`'s Google Cloud Console):

1. **IAM & Admin → Service Accounts → Create Service Account**
2. Name it e.g. `tims-desk-price-writer`, no special role needed (no GCP IAM role grants Sheets access — Sheets uses its own sharing model, same as sharing with any Google account)
3. Open the new service account → **Keys → Add Key → Create new key → JSON** — downloads a `.json` file
4. Note the service account's email, shown on its details page (looks like `tims-desk-price-writer@solid-linker-505403-j8.iam.gserviceaccount.com`)
5. Enable the **Google Sheets API** for this project if it isn't already (it should be, from the earlier WS sync setup)

### 2. Share the price sheet with the service account

The sheet **"Tim's Desk Prices"** already exists (Claude created it). Share it with the service account's email as **Editor** — Claude can do this via the Drive connector once given the email, or do it yourself: open the sheet → Share → paste the `...iam.gserviceaccount.com` address → Editor.

### 3. Add the GitHub secret

In the GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:
- Name: `GOOGLE_SERVICE_ACCOUNT_KEY`
- Value: the entire contents of the downloaded JSON key file

Never commit that JSON file — `.gitignore` already excludes `*credentials*.json` but double-check before any `git add`.

### 4. Test locally (optional, before relying on the scheduled run)

```bash
pip install -r requirements.txt
export GOOGLE_SERVICE_ACCOUNT_KEY="$(cat /path/to/the-downloaded-key.json)"
python scripts/fetch_prices.py
```

Check `data/prices.json` and `data/history/` were written, and that the "Current" / "History" tabs in the Sheet updated.

### 5. Trigger once manually on GitHub

Actions tab → "Fetch prices" workflow → **Run workflow** (the `workflow_dispatch` trigger), to confirm the secret and permissions work before waiting for the cron schedule.

## Repo layout

```
config/tickers.json      # the watchlist - symbol, display name, currency, data source
scripts/fetch_prices.py  # the cron job's entry point
scripts/sheets_writer.py # thin Sheets API wrapper (service account auth)
data/prices.json         # latest snapshot, committed each run for history/debugging
data/history/*.json      # per-symbol daily closes
reference/tim-desk.html  # the current claude.ai artifact's source, for reference
legacy/ws-sync/          # the WS transaction sync pipeline, pending Phase 3 port to Apps Script
```
