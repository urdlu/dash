"""Tim's Desk price job: fills the "Tim's Desk Prices" sheet so it matches the
dashboard's contract (see CLAUDE.md, "The Sheet contract").

Symbols and groups are read FROM the sheet's Current tab - never hard-coded.
Groups are separated by blank rows (an optional "group" column overrides
that). Blank rows and any columns this job doesn't own are left untouched.

Modes:
  full      (default) Current + History (~1y daily) + HistoryWeekly (5y)
            + Intraday (latest session, 5-min) + Yields (weekly TTM, 5y);
            also writes data/*.json for the repo record and the morning push.
  intraday  Current prices + Intraday only; exits early outside US/Canadian
            market hours. Used by the every-30-min market-hours cron.

Deterministic, no LLM. Runs on GitHub Actions; see .github/workflows/fetch-prices.yml.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(HERE, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
ET = ZoneInfo("America/New_York")

INTRADAY_CRON = "*/30 13-20 * * 1-5"  # must match the workflow file exactly
DAILY_DAYS = 380                      # ~1 year + margin so "1Y" isn't flagged as short
YIELD_COLS = ["yield_ttm", "yield_dist"]


# ---------------------------------------------------------------- the Current tab

def parse_current(values):
    """values = rows from the Current tab (header first; blank rows are []).
    Returns (header, rows, groups). rows: [{"r": sheet_row_1based, "cells": {...}}];
    groups: [[row, ...], ...] in sheet order."""
    if not values or "symbol" not in [h.strip().lower() for h in values[0]]:
        raise SystemExit("Current tab has no 'symbol' header - refusing to touch it.")
    header = [h.strip() for h in values[0]]
    lower = [h.lower() for h in header]
    rows = []
    for i, raw in enumerate(values[1:], start=2):
        cells = {lower[j]: (raw[j].strip() if j < len(raw) else "") for j in range(len(header))}
        rows.append({"r": i, "cells": cells})

    if "group" in lower:
        order, by = [], {}
        for row in rows:
            if not row["cells"].get("symbol"):
                continue
            g = row["cells"].get("group") or "Other"
            if g not in by:
                by[g] = []
                order.append(g)
            by[g].append(row)
        groups = [by[g] for g in order]
        names = order
    else:
        groups, cur = [], []
        for row in rows:
            if not row["cells"].get("symbol"):
                if cur:
                    groups.append(cur)
                    cur = []
                continue
            cur.append(row)
        if cur:
            groups.append(cur)
        names = [None] * len(groups)
    return header, rows, groups, names


def income_group_index(names, groups):
    """The income group: a group named like "income" if a group column exists,
    else the second blank-row block (the dashboard's second panel)."""
    for i, n in enumerate(names):
        if n and re.search(r"income", n, re.I):
            return i
    return 1 if len(groups) > 1 else None


# ---------------------------------------------------------------- symbol -> Yahoo

def yahoo_candidates(sym, currency):
    s = sym.strip().upper()
    if "/" in s:                      # FX pair, e.g. USD/CAD -> USDCAD=X
        a, b = s.split("/", 1)
        return [f"{a}{b}=X"]
    if "." in s or "=" in s or s.startswith("^"):
        return [s]                    # already a Yahoo ticker
    cur = (currency or "").strip().upper()
    if cur == "CAD":
        return [f"{s}.TO"]
    if cur == "USD":
        return [s]
    return [s, f"{s}.TO"]             # unknown: US listing first, then TSX


def currency_of(yahoo):
    if yahoo.endswith("=X"):
        return yahoo[3:6]
    if yahoo.endswith((".TO", ".V", ".NE", ".CN")):
        return "CAD"
    return "USD"


def _closes(df, tickers):
    """Close prices as a DataFrame with one column per ticker (handles 1 ticker)."""
    if df is None or df.empty:
        return pd.DataFrame(columns=tickers)
    c = df["Close"]
    if isinstance(c, pd.Series):
        c = c.to_frame(tickers[0])
    return c.reindex(columns=tickers)


def download(tickers, **kw):
    if not tickers:
        return pd.DataFrame()
    df = yf.download(tickers, auto_adjust=False, progress=False, threads=True, **kw)
    return _closes(df, tickers)


def resolve(symbols, currencies, daily_kw):
    """Pick a Yahoo ticker per sheet symbol by trying candidates until one has data.
    Returns ({sym: yahoo}, daily_closes keyed by sym)."""
    chosen, pending = {}, {s: yahoo_candidates(s, currencies.get(s)) for s in symbols}
    daily = pd.DataFrame()
    for _ in range(3):
        batch = {s: c[0] for s, c in pending.items() if c}
        if not batch:
            break
        df = download(sorted(set(batch.values())), **daily_kw)
        nxt = {}
        for s, y in batch.items():
            col = df[y] if y in df else pd.Series(dtype=float)
            if col.notna().any():
                chosen[s] = y
                daily[s] = col
            else:
                nxt[s] = pending[s][1:]
        pending = nxt
    for s, c in pending.items():
        print(f"[warn] no Yahoo data for {s} (tried {yahoo_candidates(s, currencies.get(s))})", file=sys.stderr)
    return chosen, daily


# ---------------------------------------------------------------- yields

def payments_per_year(div_dates):
    if len(div_dates) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(div_dates[-8:], div_dates[-7:])]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    per = 365 / median(gaps)
    return min([1, 2, 4, 12, 52], key=lambda f: abs(f - per))


def dividends(yahoo):
    try:
        d = yf.Ticker(yahoo).dividends
    except Exception as e:  # network/parse hiccup: no yields this run, keep going
        print(f"[warn] dividends for {yahoo}: {e}", file=sys.stderr)
        return pd.Series(dtype=float)
    if d is None or d.empty:
        return pd.Series(dtype=float)
    d.index = pd.to_datetime([t.date() for t in d.index])
    return d.groupby(level=0).sum().sort_index()


def ttm_sum(divs, end):
    if divs.empty:
        return 0.0
    start = end - pd.Timedelta(days=365)
    return float(divs[(divs.index > start) & (divs.index <= end)].sum())


# ---------------------------------------------------------------- formatting

def rnd(v, fx=False):
    if v is None or pd.isna(v):
        return ""
    v = float(v)
    return round(v, 4) if fx or abs(v) < 10 else round(v, 2)


def wide_rows(first_col, index_strs, frame, syms, fx_syms):
    rows = [[first_col] + syms]
    for key, (_, rec) in zip(index_strs, frame.iterrows()):
        vals = [rnd(rec.get(s), s in fx_syms) for s in syms]
        if any(v != "" for v in vals):
            rows.append([key] + vals)
    return rows


def market_open_now(now_utc):
    et = now_utc.astimezone(ET)
    if et.weekday() >= 5:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 25 <= minutes <= 16 * 60 + 10


# ---------------------------------------------------------------- main build

def build(values, mode, now_utc):
    header, rows, groups, names = parse_current(values)
    lower = [h.lower() for h in header]
    inc_i = income_group_index(names, groups)
    sym_rows = [row for g in groups for row in g]
    symbols = list(dict.fromkeys(row["cells"]["symbol"].upper() for row in sym_rows))
    income = set(row["cells"]["symbol"].upper() for row in groups[inc_i]) if inc_i is not None else set()
    currencies = {row["cells"]["symbol"].upper(): row["cells"].get("currency", "") for row in sym_rows}

    daily_kw = ({"start": (now_utc.date() - timedelta(days=DAILY_DAYS)).isoformat(), "interval": "1d"}
                if mode == "full" else {"period": "10d", "interval": "1d"})
    yahoo, daily = resolve(symbols, currencies, daily_kw)
    have = [s for s in symbols if s in yahoo]
    if not have:
        raise SystemExit("No prices fetched for any symbol - nothing written.")
    fx = {s for s in have if yahoo[s].endswith("=X")}
    daily = daily.reindex(columns=have)

    def to_sym(frame):
        return pd.DataFrame({s: frame[yahoo[s]] for s in have if yahoo[s] in frame})

    intraday = to_sym(download(sorted(set(yahoo.values())), period="1d", interval="5m"))
    # 6y of weekly closes: the extra year lets the 5y Yields series start a full
    # 5 years back (a TTM yield needs 12 months of history before it).
    weekly6 = to_sym(download(sorted(set(yahoo.values())), period="6y", interval="1wk")) if mode == "full" else None
    weekly = weekly6[weekly6.index >= pd.Timestamp(now_utc.date()) - pd.DateOffset(years=5)] if mode == "full" else None
    divs = {s: dividends(yahoo[s]) for s in have if s in income}

    # ---- Current: per-cell updates only (keeps blank rows, formulas, extra columns)
    new_header = list(header)
    if income:
        for c in YIELD_COLS:
            if c not in lower:
                new_header.append(c)
    col = {h.lower(): i for i, h in enumerate(new_header)}
    updates = {}   # (row, col_index) -> value
    for i, h in enumerate(new_header):
        if i >= len(header):
            updates[(1, i)] = h
    as_of = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = []
    today = pd.Timestamp(now_utc.date())
    for row in sym_rows:
        s = row["cells"]["symbol"].upper()
        if s not in have:
            continue
        series = daily[s].dropna()
        price = float(series.iloc[-1])
        prev = float(series.iloc[-2]) if len(series) > 1 else price
        rec = {
            "price": rnd(price, s in fx),
            "currency": currency_of(yahoo[s]),
            "change_pct": round((price / prev - 1) * 100, 4) if prev else 0.0,
            "as_of": as_of,
            "sample": "FALSE",
        }
        if "spark" in col:
            rec["spark"] = ",".join(str(rnd(v, s in fx)) for v in series.iloc[-7:])
        if not row["cells"].get("name") and "name" in col:
            try:
                info = yf.Ticker(yahoo[s]).info
                rec["name"] = info.get("longName") or info.get("shortName") or ""
            except Exception:
                pass
        if s in income:
            d = divs.get(s, pd.Series(dtype=float))
            ttm = ttm_sum(d, today)
            ppy = payments_per_year(list(d.index))
            rec["yield_ttm"] = round(ttm / price * 100, 2) if ttm else ""
            rec["yield_dist"] = round(float(d.iloc[-1]) * ppy / price * 100, 2) if ppy and not d.empty else ""
        for k, v in rec.items():
            if k in col:
                updates[(row["r"], col[k])] = v
        snapshot.append({"group": next(gi for gi, g in enumerate(groups) if row in g) + 1,
                         "symbol": s, "yahoo": yahoo[s],
                         "name": rec.get("name") or row["cells"].get("name", ""),
                         **{k: rec[k] for k in ("price", "currency", "change_pct", "as_of")},
                         **({k: rec[k] for k in YIELD_COLS} if s in income else {})})

    tabs = {}
    # ---- Intraday (latest session, 5-min, ISO UTC times)
    if not intraday.empty:
        idx = intraday.index.tz_convert("UTC") if intraday.index.tz else intraday.index.tz_localize("UTC")
        tabs["Intraday"] = wide_rows("time", [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in idx], intraday, have, fx)

    if mode == "full":
        tabs["History"] = wide_rows("date", [t.strftime("%Y-%m-%d") for t in daily.index], daily, have, fx)
        tabs["HistoryWeekly"] = wide_rows("date", [t.strftime("%Y-%m-%d") for t in weekly.index], weekly, have, fx)
        inc_syms = [s for s in have if s in income]
        if inc_syms:
            yrows = [["date"] + inc_syms]
            for t, rec in weekly.iterrows():
                anchor = min(pd.Timestamp(t) + pd.Timedelta(days=4), today)
                vals = []
                for s in inc_syms:
                    first = weekly6[s].first_valid_index()
                    px = rec.get(s)
                    if first is None or pd.isna(px) or anchor - pd.Timestamp(first) < pd.Timedelta(days=365):
                        vals.append("")
                    else:
                        vals.append(round(ttm_sum(divs[s], anchor) / float(px) * 100, 2))
                if any(v != "" for v in vals):
                    yrows.append([t.strftime("%Y-%m-%d")] + vals)
            tabs["Yields"] = yrows

    return {"updates": updates, "header_width": len(new_header), "tabs": tabs,
            "snapshot": snapshot, "daily": daily, "have": have}


def write_local(result):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "prices.json"), "w", encoding="utf-8") as f:
        json.dump(result["snapshot"], f, indent=2)
    keep = set()
    for s in result["have"]:
        name = s.replace("/", "-") + ".json"
        keep.add(name)
        series = result["daily"][s].dropna()
        with open(os.path.join(HISTORY_DIR, name), "w", encoding="utf-8") as f:
            json.dump([[d.strftime("%Y-%m-%d"), rnd(v)] for d, v in series.items()], f)
    for name in os.listdir(HISTORY_DIR):
        if name.endswith(".json") and name not in keep:
            os.remove(os.path.join(HISTORY_DIR, name))


def push(result):
    import sheets_writer as sw
    data = [{"range": f"Current!{sw.col_letter(c + 1)}{r}", "values": [[v]]}
            for (r, c), v in sorted(result["updates"].items())]
    if data:
        sw._svc().spreadsheets().values().batchUpdate(
            spreadsheetId=sw.SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    for title, rows in result["tabs"].items():
        sw.write_table(title, rows, raw=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "intraday"])
    ap.add_argument("--current-json", help="dry run: read Current rows from this JSON file instead of the sheet")
    ap.add_argument("--dry-run", action="store_true", help="don't write the sheet or data/")
    args = ap.parse_args()

    mode = args.mode or ("intraday" if os.environ.get("CRON_SCHEDULE") == INTRADAY_CRON else "full")
    now = datetime.now(timezone.utc)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"mode={mode}\n")
    if mode == "intraday" and not market_open_now(now) and not args.dry_run:
        print("Intraday run outside market hours - nothing to do.")
        return

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if args.current_json:
        values = json.load(open(args.current_json, encoding="utf-8"))
    else:
        import sheets_writer as sw
        values = sw.get_values("Current!A1:ZZ")

    result = build(values, mode, now)
    print(f"mode={mode}: {len(result['have'])} symbols, {len(result['updates'])} Current cells, "
          + ", ".join(f"{t} {len(r) - 1} rows" for t, r in result["tabs"].items()))
    if args.dry_run:
        return result
    if mode == "full":
        write_local(result)
    push(result)
    print("Done.")


if __name__ == "__main__":
    main()
