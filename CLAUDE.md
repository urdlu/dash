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

### 1. Dashboard: "Tim's Desk" (a claude.ai artifact)

- URL: https://claude.ai/artifact/HUWMhdji1szrsFyCkpsfUz (private to Tim)
- Source: `reference/tim-desk.html`. It's a single HTML file that runs inside claude.ai's artifact viewer.
- **Panel 1, Google Drive activity (live):** lists recently changed Drive files and counts how many changed today and in the last 7 days. It calls Tim's Google Drive connector directly from the page (`list_recent_files`, refreshed every 5 minutes while open). This costs no tokens.
- **Panel 2, Market watchlist:** reads rows from the artifact's built-in database, collection `watchlist`. Each row has these fields:
  `{order, symbol, name, price, currency, change_pct, spark[≤7], as_of (ISO UTC), sample (bool)}`
  - Current rows: BTC, ETH, SOL, USD/CAD (real prices since 2026-09-30) and XEQT, VFV (still sample placeholders because the web search couldn't find reliable quotes for them).
- The `window.claude.use(...)` calls (`mcp`, `db`) only work inside claude.ai. **A self-hosted version needs its own data layer.**
- Design: Hanken Grotesk + JetBrains Mono, cool blue-grey neutrals, full light/dark themes, a two-panel grid that stacks on phones. Worth keeping the look.

### 2. Scheduled task: "Tim's Desk quote refresh" (claude.ai cloud)

- ID `trig_01MvnswauVTcGWsfV8LPEv5C`, runs at 6:50am and 1:50pm PT daily, cloud-only.
- Starts a Claude session that looks up quotes with **web search** and writes them to the `watchlist` database. It works, but it costs tokens on every run and isn't reliable for TSX ETFs.
- **Plan: disable it once the new price pipeline is live.** (Manage it in claude.ai → scheduled tasks, or ask Claude to disable it by ID.)

### 3. Wealthsimple transaction sync (a Claude desktop app scheduled task, on the laptop)

- A Python pipeline (`ws_sync.py`, `ws_parser.py`, `gmail_client.py`, `sheets_client.py`), archived here under `legacy/ws-sync/` for reference. OAuth client ID/secret were scrubbed from these copies (see each file's header comment) — real values live only in the original session, never in this repo.
- It checks the Gmail inbox `wstradesws@gmail.com` (Wealthsimple trade confirmations forwarded from Tim's main address) and parses new emails. It then appends rows to the "Untitled" tab of the Google Sheet **"WS Transactions Log"** (ID `15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw`). It's idempotent, using `sync_state.json` to track processed message IDs.
- Problems: it needs the laptop on, it spends Claude tokens just to run a script, and its OAuth refresh token keeps dying (`invalid_grant`). That last one is very likely because the Google Cloud app is unverified and in "Testing" mode, where refresh tokens expire after 7 days.
- **Recommendation:** port it to **Google Apps Script** with a time-driven trigger. That's free, runs in Google's cloud, has native Gmail and Sheets access with no refresh tokens, and can store processed IDs in the Sheet or in Script Properties. Keep `ws_parser.py`'s parsing rules exactly the same. Then turn off the desktop task.

### Things already tried

- Fetching prices from the CoinGecko public API with Claude's web-fetch tool was blocked by robots.txt. That doesn't apply to normal code, which can call the CoinGecko API directly.

---

## Suggested target architecture (for Claude Code to confirm or improve)

```
GitHub Actions (cron, free)                 Google Apps Script (free)
  ├─ fetch prices (yfinance / CoinGecko)      └─ WS email → "WS Transactions Log" sheet
  ├─ compute signals / screener
  ├─ write data/*.json (or a small DB)
  └─ send morning push (ntfy / Pushover / Telegram)
                │
                ▼
     Private dashboard site (reads the JSON)
     e.g. Cloudflare Pages + Cloudflare Access (free, login-gated)
```

Points to weigh:

- **Scheduler:** GitHub Actions cron is free for private repos within the monthly minutes, which is plenty for a few runs a day. Cron times are in UTC, so convert from PT. Pacific time shifts between UTC−7 and UTC−8.
- **Prices:** `yfinance` covers TSX tickers (`XEQT.TO`, `VFV.TO`) and US stocks. The CoinGecko API covers crypto in CAD. Store daily closes so the charts can show history.
- **Charts:** a small front end with a real charting library (e.g. uPlot, Lightweight Charts or Chart.js) that reads the JSON history.
- **Phone push:** ntfy.sh is free (app plus an HTTP POST, and the topic name must be unguessable). Pushover is a small one-time fee and more polished. A Telegram bot is also free. Any of them works with the laptop off.
- **Trade suggestions:** start with rules and no LLM. Examples: Income Screener-style premium-yield ranking, moves beyond X%, distance from the 50/200-day averages, RSI. Optionally add one small Claude API call to write a 3-line morning summary. Label everything as suggestions for Tim to evaluate, not advice.
- **Hosting privacy:** GitHub Pages sites from a free account are public, so don't use them for portfolio data. Cloudflare Pages + Access (free tier), or any host with a login in front, works.
- **Alternative:** keep the claude.ai artifact as the front end and have the pipeline write to a Google Sheet, which the artifact reads through the Drive connector at no token cost. It's simpler, but less room to grow than a self-hosted site.

## Phased plan

1. **Phase 1 — data + plots:** set up the repo and a GitHub Actions job to fetch daily prices for the watchlist. Store the history, build the private dashboard with the watchlist and charts, and port the current look. Then disable the claude.ai quote-refresh task.
2. **Phase 2 — morning push:** a job at about 6:00am PT that computes signals and sends a phone notification linking to the dashboard.
3. **Phase 3 — WS transactions:** port the sync to Apps Script, retire the desktop task, and add a "Recent trades" panel.
4. **Phase 4 — Income Screener signals:** feed the screener's covered call and CSP rankings into the morning push and a dashboard panel. SnapTrade credentials go in secrets.

## Open questions to ask Tim

1. The exact tickers to track: his ETFs (XEQT and VFV are placeholders), the option underlyings his Income Screener uses, and other stocks.
2. Push channel: ntfy, Pushover or Telegram?
3. Hosting: a self-hosted private site (Cloudflare Pages + Access), or keep the claude.ai artifact as the front end?
4. Wake-up time for the morning push, and weekdays only or every day?
5. Where the Income Screener code lives, and whether it should move into this repo or stay separate.
