"""Fetches current price + daily history for every ticker in
config/tickers.json, writes local data/*.json for the repo's own record, and
pushes the same data to the "Tim's Desk Prices" Google Sheet (two tabs:
"Current" - one row per symbol, matching the artifact's watchlist row shape;
"History" - wide daily-close table the dashboard's chart panel reads).

Deterministic, no LLM calls. Meant to run under GitHub Actions on a cron.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
import sheets_writer  # noqa: E402

HERE = os.path.dirname(os.path.dirname(__file__))
TICKERS_PATH = os.path.join(HERE, "config", "tickers.json")
DATA_DIR = os.path.join(HERE, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
HISTORY_DAYS = 90
SPARK_POINTS = 7


def load_tickers():
    with open(TICKERS_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_yfinance(t):
    tk = yf.Ticker(t["yf_ticker"])
    hist = tk.history(period=f"{HISTORY_DAYS + 10}d")
    if hist.empty:
        raise RuntimeError(f"no yfinance data for {t['yf_ticker']}")
    closes = hist["Close"].dropna()
    series = [(d.strftime("%Y-%m-%d"), round(float(v), 4)) for d, v in closes.items()]
    series = series[-HISTORY_DAYS:]
    price = series[-1][1]
    prev = series[-2][1] if len(series) > 1 else price
    change_pct = ((price - prev) / prev * 100) if prev else 0.0
    return price, change_pct, series


def fetch_coingecko(t):
    cid = t["coingecko_id"]
    r = requests.get(
        f"https://api.coingecko.com/api/v3/coins/{cid}/market_chart",
        params={"vs_currency": "cad", "days": HISTORY_DAYS, "interval": "daily"},
        timeout=30,
    )
    r.raise_for_status()
    prices = r.json()["prices"]  # [[ms, price], ...]
    series = []
    for ms, price in prices:
        d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        series.append((d, round(float(price), 2)))
    # de-dupe same-day points (CoinGecko sometimes includes a partial "today")
    dedup = {}
    for d, p in series:
        dedup[d] = p
    series = sorted(dedup.items())[-HISTORY_DAYS:]
    price = series[-1][1]
    prev = series[-2][1] if len(series) > 1 else price
    change_pct = ((price - prev) / prev * 100) if prev else 0.0
    return price, change_pct, series


def build_rows(tickers):
    current_rows = []
    history_by_symbol = {}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for t in tickers:
        try:
            if t["source"] == "yfinance":
                price, change_pct, series = fetch_yfinance(t)
            elif t["source"] == "coingecko":
                price, change_pct, series = fetch_coingecko(t)
                time.sleep(1.5)  # be polite to CoinGecko's free tier rate limit
            else:
                raise ValueError(f"unknown source {t['source']}")
        except Exception as e:
            print(f"[warn] {t['symbol']}: {e}", file=sys.stderr)
            continue

        spark = [p for _, p in series[-SPARK_POINTS:]]
        current_rows.append({
            "order": t["order"],
            "symbol": t["symbol"],
            "name": t["name"],
            "price": price,
            "currency": t["currency"],
            "change_pct": round(change_pct, 4),
            "spark": spark,
            "as_of": now_iso,
            "sample": False,
        })
        history_by_symbol[t["symbol"]] = dict(series)

        # local record, per-symbol
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(os.path.join(HISTORY_DIR, f"{t['symbol'].replace('/', '-')}.json"), "w", encoding="utf-8") as f:
            json.dump(series, f, indent=2)

    return current_rows, history_by_symbol


def write_local_snapshot(current_rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "prices.json"), "w", encoding="utf-8") as f:
        json.dump(current_rows, f, indent=2)


def push_to_sheet(current_rows, history_by_symbol):
    header = ["order", "symbol", "name", "price", "currency", "change_pct", "spark", "as_of", "sample"]
    rows = [header]
    for r in current_rows:
        rows.append([
            r["order"], r["symbol"], r["name"], r["price"], r["currency"],
            r["change_pct"], ",".join(str(v) for v in r["spark"]), r["as_of"], str(r["sample"]).lower(),
        ])
    sheets_writer.update_range(f"Current!A1:I{len(rows)}", rows)

    sheets_writer.ensure_sheet_exists("History")
    all_dates = sorted(set(d for series in history_by_symbol.values() for d in series))
    symbols = [t["symbol"] for t in load_tickers() if t["symbol"] in history_by_symbol]
    hist_rows = [["date"] + symbols]
    for d in all_dates:
        hist_rows.append([d] + [history_by_symbol[s].get(d, "") for s in symbols])
    col_letter = chr(ord("A") + len(symbols))  # e.g. 10 symbols -> up to column K
    sheets_writer.update_range(f"History!A1:{col_letter}{len(hist_rows)}", hist_rows)


def main():
    tickers = load_tickers()
    current_rows, history_by_symbol = build_rows(tickers)
    if not current_rows:
        print("no data fetched for any ticker - aborting without writing", file=sys.stderr)
        sys.exit(1)
    write_local_snapshot(current_rows)
    push_to_sheet(current_rows, history_by_symbol)
    print(f"Done. {len(current_rows)}/{len(tickers)} tickers updated.")


if __name__ == "__main__":
    main()
