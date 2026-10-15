# Tim's Desk

Tim's personal dashboard + investing automation. Goal: **low-cost, fully automated, works with the laptop off.**

## Who this is for

- Tim, British Columbia, timezone `America/Vancouver` (Pacific; switches PDT UTC−7 ↔ PST UTC−8).
- Self-directed investor who builds his own tools. Prefers step-by-step guidance and direct answers.
- Brokers: **Wealthsimple** (trade-confirmation emails → Gmail sync) and **IBKR** (read-only via Flex Web Service, STEP 2).
- Has a separate Flask "Income Screener" (SnapTrade, read-only; ranks covered calls / CSPs by annualized premium yield). Location still TBD.

## Hard rules

- **Laptop can be off.** Every scheduled job runs in the cloud: GitHub Actions or Google Apps Script.
- **Don't run deterministic jobs through Claude/any LLM.** Fetching prices, parsing emails, computing signals, sending pushes = plain code on free schedulers. An LLM call is OK only where it adds judgment (e.g. a short morning summary): one small API call per day, never an agent session.
- **Keep it private.** Repo `github.com/urdlu/dash` stays private. Dashboard is a private claude.ai artifact.
- **Never commit secrets.** They live in GitHub Actions secrets / Apps Script Properties only. Never ask Tim to paste a token in chat; never log one.
- **Suggestions, not advice.** Anything trade-related is labelled as a suggestion for Tim to evaluate. **No automated order placement, ever.**

## How to work with Tim

- Step-by-step. **Stop after each STEP**, show what changed and exactly what Tim must do (secrets, approvals, clicks), then wait.
- **Publishing the dashboard:** always publish to the same artifact link, https://claude.ai/artifact/HUWMhdji1szrsFyCkpsfUz, and **pull the latest live version first** (`Artifact read` without `path` to register it, plus `path: "index.html"` for a byte-exact copy). Other chats (Cowork) also publish to it — never overwrite it from an older local copy.
- `reference/tim-desk.html` is the repo's copy of the live page (the page content as authored, without the platform's `<!doctype…><body>` wrapper). Re-sync it from the live artifact before editing.
- `gh` CLI is installed (`C:\Program Files\GitHub CLI\gh.exe`, may not be on PATH) and authenticated as `urdlu`: use it for repo work, triggering runs, reading logs. **Never** use it to set or read secrets — Tim does that himself.

---

## The dashboard (artifact v5)

- Reads the Sheet **"Tim's Desk Prices"** (ID `1rKLxaCrsAkvYPAff77JrOjBbfr3ix2CmClVhx7td03c`) through Tim's claude.ai Google Drive connector (`read_file_content`), refetched every 5 min while open. **No tokens per refresh.**
- The page parses the connector's markdown-table text client-side (`parseTabs`): one `### Sheet Name: X` section per tab, tab names lower-cased. Fragile by nature (not a stable contract) but accepted.
- **Two panels, one per group** on the Current tab:
  1. **Corporate class ETFs** — HXS, HXT, HBB, HXCN, HXQ
  2. **Income ETFs** — SPYI, QQQI, JEPI, JEPQ, SCHD
- Each panel: multi-line chart of **% change from the start of the range**, range buttons **1D / 5D / 1M / 6M / 1Y / 5Y**, and a table (Symbol, Name, Last, Day, Period). The income panel adds **Yield (TTM)**, **Dist. yield**, **Yield 1y ago** columns and a **yield-history** chart.
- Range → source tab: `1D` → `Intraday` (button disabled if the tab is empty); `5Y` → `HistoryWeekly` (falls back to `History`); everything else → `History`. Yield chart + "Yield 1y ago" → `Yields`.
- Declared capabilities (v5): `mcp: {servers: [{server: "Google Drive", tools: ["download_file_content", "read_file_content"]}]}`.
- Design: Hanken Grotesk + JetBrains Mono, cool blue-grey neutrals, full light/dark themes, 8-colour series palette `--s1…--s8`. New panels (e.g. Portfolio) must match it.

## The Sheet contract (what the price job must produce)

**Current** tab — columns `order, symbol, name, price, currency, change_pct, spark, as_of, sample` (+ income-only `yield_ttm, yield_dist`).
- **Groups are separated by a BLANK ROW.** An optional `group` column overrides blank-row grouping. Preserve the blank rows.
- **Symbols are read FROM this tab — never hard-coded.** Anything Tim adds gets picked up automatically.
- Every symbol: `name`, `price`, `currency` (income ETFs are US-listed → `USD`; TSX ones → `CAD`), `change_pct`, `as_of` (ISO UTC), `sample=FALSE`.
- Income group only: `yield_ttm` = sum of last 12 months of distributions ÷ price, in %; `yield_dist` = latest distribution × payments/year ÷ price, in %.
- Data source: `yfinance`. TSX tickers need `.TO` when fetching; the Sheet keeps the plain symbol.

**History** — `date` (YYYY-MM-DD), then one column per symbol (both groups). Daily closes, ~1 year. Keep it to ~1 year so the Drive connector returns it in full; if it starts truncating, tell Tim.
**HistoryWeekly** — same layout, weekly closes, 5 years (used by the 5Y button).
**Intraday** — `time` (ISO), then one column per symbol; today's 5-minute prices (turns on the 1D button).
**Yields** — `date`, then one column per income ETF; weekly TTM yield %, up to 5 years, from distribution history.

Schedule: weekdays after the close, plus once before 6am PT. Cron is UTC — handle PST/PDT (the two-cron + offset-check trick used by `morning-push.yml` works). **No intraday schedule** — Tim decided daily is enough (2026-10-13).

---

## What exists in the repo

### Price job (GitHub Actions) — STEP 1 done 2026-10-13
- `.github/workflows/fetch-prices.yml` → `scripts/fetch_prices.py` + `scripts/sheets_writer.py`. Symbols + groups are read from the Current tab (no config file). Current is updated **cell by cell** (never cleared), so blank rows and hand edits survive. History / HistoryWeekly / Intraday / Yields are machine-owned and rewritten whole, **newest row first**.
- Crons (UTC): `15 21 * * 1-5` full after close; `0 3,8 * * 2-6` overnight full backups (before 6am PT in both PDT/PST, survives GitHub's late firing); no intraday cron (Tim: daily is enough). Full runs still fill the Intraday tab with the last session's 5-min bars. `concurrency: prices`. `workflow_dispatch` input `mode` = full|intraday.
- Auth: Google Cloud **service account** `tims-desk-price-writer@solid-linker-505403-j8.iam.gserviceaccount.com` (Editor on the Sheet), key in secret `GOOGLE_SERVICE_ACCOUNT_KEY`. No OAuth, no token expiry.
- **GitHub's scheduled runs fire hours late** (observed 1–6 h); the overnight backups cover that.
- **Connector truncation:** `read_file_content` returns only ~48 data rows per tab (and cuts cells at ~52 chars). Newest-first ordering means the dashboard gets the latest ~48 rows (History ≈ 2 months, HistoryWeekly/Yields ≈ 11 months, Intraday ≈ last 4 h). `download_file_content` with `exportMimeType: application/vnd.oasis.opendocument.spreadsheet` returns **every tab in full** (~58 KB ODS zip, base64) — **the dashboard (v5, 2026-10-13) reads that**: it unzips the ODS with the browser's `DecompressionStream` and walks `content.xml` (`parseOds`), falling back to `read_file_content` + `parseTabs` only if the export fails (stamp then says "partial history").
- `data/prices.json`, `data/history/*.json` are committed by full runs (debug record; the dashboard doesn't read them). `morning_push.py` still reads the deleted `config/tickers.json` — switch it to `data/prices.json` in STEP 4.

### IBKR Flex job (STEP 2, built 2026-10-14 — waiting on Tim's IBKR setup + secrets)
- `.github/workflows/ibkr-flex.yml` → `scripts/ibkr_flex.py` (stdlib HTTP/XML). Crons `30 10` + `0 13` UTC Tue–Sat (before 6am PT, with backup) and `45 21` UTC Mon–Fri (trade confirms). Exits quietly while secrets are unset.
- Writes **Positions** (summary rows, one per holding), **Trades** (merged by `tradeID`, all history kept, newest first), **Account** (NAV, day change ex-deposits, cash, token days left, warning).
- Token expiry: `IBKR_TOKEN_EXPIRES=YYYY-MM-DD` (Tim set it as a secret; the workflow reads a variable or a secret); within 14 days → warning in Account tab + one GitHub issue "Renew the IBKR Flex token" (GitHub emails Tim).
- Next: after the first real run, build the dashboard **Portfolio** panel from the real tab shapes.

### Morning push (built 2026-10-12, not yet live)
- `scripts/morning_push.py` (stdlib only, no LLM) + `morning-push.yml` at repo root (**move to `.github/workflows/`**). Two UTC crons (12:55 / 13:55 weekdays); the script keeps only the one matching today's Vancouver offset → 5:55am PT year-round. Signals: 1-day move (≥1.5%, FX ≥0.5%), RSI(14) ≤30/≥70, 50-day SMA crossings, within 1% of 90-day high/low, stale-data warning (>40 h). Needs secret `NTFY_TOPIC`. Channel: ntfy.sh (decided earlier; STEP 4 says confirm with Tim).
- Built against the old 6-symbol Current tab — re-check it against the v4 Sheet contract before turning on.

### Wealthsimple email sync
- **Old:** Python (`legacy/ws-sync/`, secrets scrubbed) running as a Claude desktop scheduled task on the laptop; Gmail `wstradesws@gmail.com` → Sheet **"WS Transactions Log"** (ID `15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw`), "Untitled" tab. Needs the laptop, burns tokens, OAuth app was in Testing mode (7-day tokens; it was later published to Production, but the desktop-task problems remain).
- **New (built 2026-10-12, not yet deployed):** `apps-script/ws-sync/` — Apps Script port, parity-tested against the Python (`test/parity_test.py`, 18/18 identical). Hourly time-driven trigger in wstradesws@gmail.com; state in hidden `_ws_sync_state` tab; first run de-dupes against existing log rows. Optional ntfy on new trades via Script Property `NTFY_TOPIC`. Parsing rules must stay identical to `ws_parser.py` (incl. multi-leg "Options strategy:" fills).
- After the Apps Script version is live and verified, Tim turns off the desktop task.

### Other cloud tasks
- "Tim's Desk quote refresh" (`trig_01MvnswauVTcGWsfV8LPEv5C`) — **disabled** 2026-10-02 (replaced by the price job).
- "Claude Queue runner" (`trig_01VDvmdhS7q5u9SVpmK8aYgS`, 7:08am PT daily) + artifact https://claude.ai/artifact/E4sZHcSDYkNKCb2MhvXTHx (db collection `queue`) — judgment work only (read/research/draft); exits immediately when the queue is empty. Never trades or sends anything.

---

## Plan

**STEP 0 — orient** (this file, v4 saved to `reference/tim-desk.html`). ✅ 2026-10-13.

**STEP 1 — price job matches the Sheet contract exactly.** Read symbols + groups from Current (preserve blank rows / `group` column); fill name/price/currency/change_pct/as_of/sample; income yields; History (~1y daily), HistoryWeekly (5y weekly), Intraday (today, 5-min), Yields (weekly TTM %, 5y). DST-proof schedule incl. a reliable pre-6am-PT run. Run once, read the Sheet back, show Tim a sample of each tab. ✅ 2026-10-13 Dashboard v5 reads the full ODS export.

**STEP 2 — IBKR read-only via Flex Web Service** (not the live API). Activity Flex Query (Open Positions, Trades, Cash Report, Change in NAV; XML; Last Business Day) + Trade Confirmation Flex Query. Secrets (Tim sets them): `IBKR_FLEX_TOKEN`, `IBKR_FLEX_ACTIVITY_ID`, `IBKR_FLEX_TRADES_ID`. Job: `SendRequest` → poll `GetStatement` with backoff → parse XML → write **Positions** and **Trades** tabs to the same Sheet. Then a **Portfolio** panel (holdings, value, day change, recent trades) matching the design. Remind Tim **2 weeks before the token expires** (1-year life). Live API (IB Gateway + IBC + ib_async on a VPS) only if Tim asks later.

**STEP 3 — WS sync off the laptop.** Deploy the Apps Script port (already built, see above); Tim provides/approves; then he disables the desktop task.

**STEP 4 — morning phone push** before 6am PT with rule-based suggestions (labelled as suggestions). Ask Tim which channel (ntfy / Pushover / Telegram) — ntfy.sh was chosen earlier and `morning_push.py` already targets it.

Still open: where the Income Screener code lives / whether it joins this repo.
