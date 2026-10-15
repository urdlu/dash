"""Phase A data: 10 years of daily OHLCV for current S&P 500 members + SPY,
and earnings dates, cached under research/gap_study/data/ (git-ignored).

Run:  python research/gap_study/fetch.py            (prices + earnings)
      python research/gap_study/fetch.py --no-earnings

Caveat baked into the data: the universe is TODAY's S&P 500, so companies
that were dropped or delisted over the decade are missing (survivorship bias).
"""
import argparse
import re
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

DATA = Path(__file__).parent / "data"
UA = {"User-Agent": "tims-desk-research/1.0"}


def sp500_tickers() -> list[str]:
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=UA, timeout=30).text
    table = html.split('id="constituents"', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
    out = []
    for r in rows:
        cell = re.search(r"<td[^>]*>(.*?)</td>", r, re.S)
        if cell:
            sym = re.sub(r"<[^>]+>", "", cell.group(1)).strip()
            if sym:
                out.append(sym.replace(".", "-"))  # BRK.B -> BRK-B (Yahoo style)
    if len(out) < 450:
        raise SystemExit(f"only parsed {len(out)} tickers - Wikipedia layout changed?")
    return sorted(set(out))


def fetch_prices(tickers, start):
    frames = []
    for i in range(0, len(tickers), 50):
        chunk = tickers[i:i + 50]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, auto_adjust=True, progress=False, threads=True, group_by="column")
                break
            except Exception as e:
                print(f"  chunk {i}: {e}; retry")
                time.sleep(5 * (attempt + 1))
        else:
            continue
        long = df.stack(level=1, future_stack=True).reset_index()
        long.columns = [str(c).lower() for c in long.columns]
        long = long.rename(columns={"ticker": "sym", "level_1": "sym"})
        frames.append(long.dropna(subset=["open", "close"]))
        print(f"  prices {min(i + 50, len(tickers))}/{len(tickers)}")
    out = pd.concat(frames, ignore_index=True)
    return out[["date", "sym", "open", "high", "low", "close", "volume"]]


def fetch_earnings(tickers):
    rows = []
    for n, sym in enumerate(tickers, 1):
        try:
            ed = yf.Ticker(sym).get_earnings_dates(limit=48)
            if ed is not None and len(ed):
                for ts in ed.index:
                    rows.append((sym, ts.tz_convert("America/New_York").tz_localize(None)))
        except Exception as e:
            print(f"  earnings {sym}: {type(e).__name__}")
        if n % 50 == 0:
            print(f"  earnings {n}/{len(tickers)}")
        time.sleep(0.3)
    return pd.DataFrame(rows, columns=["sym", "when"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-earnings", action="store_true")
    ap.add_argument("--start", default="2015-06-01")  # 10y + room for 200-day averages
    a = ap.parse_args()
    DATA.mkdir(exist_ok=True)

    tickers = sp500_tickers()
    print(f"{len(tickers)} S&P 500 tickers")
    px = fetch_prices(tickers + ["SPY"], a.start)
    px.to_pickle(DATA / "prices.pkl")
    print(f"prices: {len(px):,} rows, {px.sym.nunique()} symbols, {px.date.min().date()}..{px.date.max().date()}")

    if not a.no_earnings:
        ed = fetch_earnings(tickers)
        ed.to_pickle(DATA / "earnings.pkl")
        print(f"earnings: {len(ed):,} dates for {ed.sym.nunique()} symbols, earliest {ed.when.min()}")


if __name__ == "__main__":
    main()
