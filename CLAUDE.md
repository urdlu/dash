# Tim's Desk

A personal dashboard project, moved out of a Claude chat and into a real repo so it can grow into an actual app.

## Who this is for

- Tim, based in British Columbia (timezone `America/Vancouver`). A self-directed investor who builds his own tools.
- Overall goal: a **low-cost, highly automated** personal assistant and investing toolkit.
- Prefers step-by-step technical guidance and direct answers.
- Uses Wealthsimple as his broker. He already has a Flask "Income Screener" app that reads Wealthsimple through SnapTrade (read-only). It screens covered calls and cash-secured puts and ranks them by annualized premium yield. It builds OCC option symbols in code because the SnapTrade SDK has no full option-chain endpoint.
- Also interested in crypto (BTC cycle analysis, on-chain indicators).

## What Tim wants

1. **A live dashboard** he can open on any device, including his phone.
2. **Plots of a list of stocks he tracks:** price history charts, not just current quotes.
3. **A morning phone notification with trade suggestions** that arrives before he wakes up and works with his laptop off.
4. **Room to grow:** more panels, and data from his other tools (Income Screener, Wealthsimple transactions).

## Hard constraints

- **The laptop can be off.** Every scheduled job has to run in the cloud.
- **Keep costs low.** Deterministic jobs (fetching prices, parsing emails, computing signals, sending pushes) must **not** run through Claude or any other LLM on each run. Use free schedulers. An LLM call is fine only where it adds judgment, such as a short written morning summary, and it should be one small API call per day, not an agent session.
- **Keep it private.** The dashboard will show portfolio and trade data, so it must not be on a public URL.
- **Never commit secrets.** API keys and OAuth tokens go in the platform's secret store (e.g. GitHub Actions secrets), never in the repo.

---

## What exists today

### 1. Dashboard: "Tim's Desk" (a claude.ai artifact) — Phase 1 complete

- URL: https://claude.ai/artifact/HUWMhdji1szrsFyCkpsfUz (private to Tim)
- Source: `reference/tim-desk.html`. It's a single HTML file that runs inside claude.ai's artifact viewer.
- **Panel 1, Google Drive activity (live):** lists recently changed Drive files and counts how many changed today and in the last 7 days. Calls the Google Drive connector's `list_recent_files` directly from the page, refreshed every 5 minutes while open. No tokens.
- **Panel 2, Market watchlist:** reads the "Current" tab of the **"Tim's Desk Prices"** Google Sheet (ID `1rKLxaCrsAkvYPAff77JrOjBbfr3ix2CmClVhx7td03c`) via the Drive connector's `read_file_content`, same free mechanism as Panel 1 — **not** the artifact's own database anymore (that `db` capability was dropped). Row shape: `{order, symbol, name, price, currency, change_pct, spark[≤7], as_of (ISO UTC), sample (bool)}`.
- **Panel 3, Price history (new):** a 90-day line chart per symbol, with pill buttons to switch tickers and a hover tooltip. Reads the same Sheet's "History" tab (wide format: `date, <symbol1>, <symbol2>, ...`).
- Both Panel 2 and 3 parse the connector's markdown-table text response client-side (see `parseSheetTables` in the script) — a real but accepted fragility, since that format isn't a stable contract.
- Capabilities declared: `mcp: {servers: [{server: "Google Drive", tools: ["list_recent_files", "read_file_content"]}]}`. No `db`.
- Design: Hanken Grotesk + JetBrains Mono, cool blue-grey neutrals, full light/dark themes, a wrapping grid that stacks on phones.
- Watchlist tickers (decided, replacing the XEQT/VFV placeholders): USD/CAD + the 5 largest Global X Corporate Class ETFs by AUM — HXS, HXT, HBB, HXCN, HXQ. Crypto (BTC/ETH/SOL) was explicitly dropped for now; the CoinGecko fetch path stays in `scripts/fetch_prices.py` (with 429 retry/backoff) for if it's reintroduced later.

### 2. Scheduled task: "Tim's Desk quote refresh" (claude.ai cloud) — disabled

- ID `trig_01MvnswauVTcGWsfV8LPEv5C`. Used to run at 6:50am/1:50pm PT, starting a Claude session that web-searched quotes and wrote them to the artifact's `db`. **Disabled** (2026-10-02) now that the GitHub Actions pipeline below replaced it — no more per-run token cost, no more unreliable TSX quotes.

### Price fetch pipeline (GitHub Actions, free) — Phase 1 complete

- Repo: `github.com/urdlu/dash` (private). `gh` CLI is set up and authenticated as `urdlu` for repo management (creating things, triggering runs, reading logs) — but never for touching secrets; that stays manual.
- `.github/workflows/fetch-prices.yml`: cron (6:50am/1:50pm PT, i.e. 13:50/20:50 UTC during PDT — adjust by 1 hour for PST, roughly Nov–Mar) + `workflow_dispatch` for manual runs. Needs `permissions: contents: write` for its own data-commit step (a real bug hit and fixed: the default `GITHUB_TOKEN` is read-only).
- `scripts/fetch_prices.py`: `yfinance` for the ETFs/FX, CoinGecko (unused while crypto is off) for crypto, writes `data/prices.json` + `data/history/*.json` locally (committed each run) and pushes both to the Sheet's Current/History tabs.
- `scripts/sheets_writer.py`: writes via a **Google Cloud service account** (project `solid-linker-505403-j8`, the same "WS Trades" project as the WS sync below) — not user OAuth, so no 7-day testing-token expiry. The service account's key lives only in the `GOOGLE_SERVICE_ACCOUNT_KEY` GitHub Actions secret, never in the repo. `ensure_sheet_exists` self-heals a sheet's default tab name (Google names a CSV-uploaded sheet's first tab "Untitled" — the pipeline expects "Current"/"History") by renaming rather than leaving orphan tabs. Every write clears the target range first, so a shrinking ticker list doesn't leave stale rows behind (both were real bugs, found and fixed via actual live test runs, not assumed).

### 3. Wealthsimple transaction sync (a Claude desktop app scheduled task, on the laptop)

- A Python pipeline (`ws_sync.py`, `ws_parser.py`, `gmail_client.py`, `sheets_client.py`), archived here under `legacy/ws-sync/` for reference. OAuth client ID/secret were scrubbed from these copies (see each file's header comment) — real values live only in the original session, never in this repo.
- It checks the Gmail inbox `wstradesws@gmail.com` (Wealthsimple trade confirmations forwarded from Tim's main address) and parses new emails. It then appends rows to the "Untitled" tab of the Google Sheet **"WS Transactions Log"** (ID `15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw`). It's idempotent, using `sync_state.json` to track processed message IDs.
- Problems: it needs the laptop on, it spends Claude tokens just to run a script, and its OAuth refresh token keeps dying (`invalid_grant`). That last one is very likely because the Google Cloud app is unverified and in "Testing" mode, where refresh tokens expire after 7 days.
- **Recommendation:** port it to **Google Apps Script** with a time-driven trigger. That's free, runs in Google's cloud, has native Gmail and Sheets access with no refresh tokens, and can store processed IDs in the Sheet or in Script Properties. Keep `ws_parser.py`'s parsing rules exactly the same. Then turn off the desktop task.

### Things already tried

- Fetching prices from the CoinGecko public API with Claude's web-fetch tool was blocked by robots.txt. That doesn't apply to normal code, which can call the CoinGecko API directly.

---

## Architecture actually used (Phase 1)

Tim chose to **keep the claude.ai artifact as the front end** rather than build a self-hosted site — simpler, and it keeps the free live Drive panel. So the shape is:

```
GitHub Actions (cron, free)
  └─ scripts/fetch_prices.py (yfinance, service-account Sheets write)
       writes → "Tim's Desk Prices" Google Sheet (Current + History tabs)
                       │
                       ▼
     claude.ai artifact "Tim's Desk" reads the Sheet via the
     Drive connector's read_file_content, free, every 5 min while open
```

No Cloudflare Pages, no self-hosted site, no static JSON site build — the Sheet *is* the data layer, and the artifact already has free read access to it through the connector the Drive panel was already using. `data/*.json` in this repo is still written and committed each run (useful local record / debugging), but the artifact doesn't read it directly.

Points still worth keeping in mind for later phases:

- **Scheduler:** GitHub Actions cron is free for private repos within the monthly minutes. Cron times are UTC; Pacific time shifts between UTC−7 (PDT) and UTC−8 (PST) — the workflow file has a comment on this, not yet handled automatically.
- **Phone push (Phase 2):** ntfy.sh — decided. Free, topic-based HTTP POST; the topic name must stay unguessable since there's no auth.
- **Trade suggestions (Phase 2+):** start with rules and no LLM. Examples: Income Screener-style premium-yield ranking, moves beyond X%, distance from the 50/200-day averages, RSI. Optionally one small Claude API call for a 3-line morning summary. Label everything as suggestions for Tim to evaluate, not advice.
- **WS transactions (Phase 3):** still planned — port `legacy/ws-sync/` to Google Apps Script (free, no OAuth refresh tokens, same trick the price pipeline uses here but even simpler since Apps Script runs natively inside Google).

## Phased plan

1. ~~**Phase 1 — data + plots.**~~ **Done (2026-10-02).** Repo set up, GitHub Actions fetching prices for USD/CAD + HXS/HXT/HBB/HXCN/HXQ, Sheet-backed watchlist and price-history panels live on the artifact, old quote-refresh task disabled.
2. **Phase 2 — morning push:** a job at **5:55am PT, weekdays only** (decided) that computes signals and sends an **ntfy.sh** (decided) notification linking to the dashboard.
3. **Phase 3 — WS transactions:** port the sync to Apps Script, retire the desktop task, and add a "Recent trades" panel.
4. **Phase 4 — Income Screener signals:** feed the screener's covered call and CSP rankings into the morning push and a dashboard panel. SnapTrade credentials go in secrets.

## Open questions to ask Tim

Answered: tickers (USD/CAD + HXS/HXT/HBB/HXCN/HXQ by AUM, crypto dropped for now), push channel (ntfy.sh), hosting (keep the claude.ai artifact), push timing (5:55am PT weekdays).

Still open:
1. Where the Income Screener code lives, and whether it should move into this repo or stay separate (relevant once Phase 4 starts).
