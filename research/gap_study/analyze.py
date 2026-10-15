"""Phase A gap study: do big opening gaps in S&P 500 stocks continue or fade?

Event = a trading day whose open is >= 2% away from the prior close (a daily-
bar proxy for the overnight + premarket move). Entry is assumed AT THE OPEN of
the gap day; exits at the close of day 0 (same day), 1, 4 and 19 (= 1, 2, 5,
20 trading days). Every return is shown MARKET-ADJUSTED: stock minus SPY over
the exact same open->close window, so a market-wide rally doesn't count as edge.

Standard errors are clustered by date (gaps bunch up on macro days), and
results are also shown after a round-trip trading cost.

Run after fetch.py:  python research/gap_study/analyze.py  -> data/results.json
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
HORIZONS = [0, 1, 4, 19]          # exit at close of day t+h (h=0 is same-day)
CLOSE_H = [1, 5, 20]              # entry at the gap day's CLOSE, exit k days later
PATH_DAYS = 21                    # event-study path, close of day 0..20
COST_BPS = 10                     # round trip; liquid large caps at the open
START = "2016-01-01"              # after 200-day warm-up
MIN_PRICE, MIN_ADV = 5.0, 5e6     # $ and average daily $ volume
MAX_ABS_GAP = 0.40                # larger is almost always a data error

UP_BUCKETS = [(0.02, 0.04, "+2–4%"), (0.04, 0.08, "+4–8%"), (0.08, MAX_ABS_GAP, "+8% or more")]
DN_BUCKETS = [(-0.04, -0.02, "−2–4%"), (-0.08, -0.04, "−4–8%"), (-MAX_ABS_GAP, -0.08, "−8% or more")]


def load():
    px = pd.read_pickle(DATA / "prices.pkl")
    px["date"] = pd.to_datetime(px["date"]).dt.tz_localize(None)
    px = px.sort_values(["sym", "date"]).reset_index(drop=True)
    return px


def add_features(px):
    g = px.groupby("sym", group_keys=False)
    px["prev_close"] = g["close"].shift(1)
    px["gap"] = px["open"] / px["prev_close"] - 1
    px["rvol"] = px["volume"] / g["volume"].transform(lambda v: v.shift(1).rolling(20, min_periods=15).mean())
    px["adv"] = g.apply(lambda d: (d["close"] * d["volume"]).shift(1).rolling(20, min_periods=15).mean())
    # did an up-gap fill (trade back to the prior close) during the day? and vice versa
    px["filled"] = np.where(px["gap"] > 0, px["low"] <= px["prev_close"], px["high"] >= px["prev_close"])
    # open of day t -> close of day t+k, for k = 0..PATH_DAYS-1
    for k in range(PATH_DAYS):
        px[f"p{k}"] = g["close"].shift(-k) / px["open"] - 1
    # close of the gap day -> close of day t+k: tradable once the day's volume is known
    for k in CLOSE_H:
        px[f"q{k}"] = g["close"].shift(-k) / px["close"] - 1
    return px


def market_adjust(px):
    spy = px[px.sym == "SPY"].set_index("date")
    spy_cols = {f"p{k}": spy[f"p{k}"] for k in range(PATH_DAYS)}
    sma200 = spy["close"].rolling(200).mean()
    regime = (spy["close"] > sma200).shift(1)  # known before the open
    stocks = px[px.sym != "SPY"].copy()
    for k in range(PATH_DAYS):
        stocks[f"x{k}"] = stocks[f"p{k}"] - stocks["date"].map(spy_cols[f"p{k}"])
    for k in CLOSE_H:
        stocks[f"y{k}"] = stocks[f"q{k}"] - stocks["date"].map(spy[f"q{k}"])
    stocks["bull"] = stocks["date"].map(regime)
    stocks["spy_gap"] = stocks["date"].map(spy["gap"])
    return stocks


def add_earnings(stocks):
    path = DATA / "earnings.pkl"
    if not path.exists():
        stocks["earn"] = np.nan
        return stocks, False
    ed = pd.read_pickle(path)
    if ed.empty:
        stocks["earn"] = np.nan
        return stocks, False
    ed["when"] = pd.to_datetime(ed["when"])
    days = pd.DatetimeIndex(sorted(stocks["date"].unique()))
    keys = set()
    for sym, when in ed.itertuples(index=False):
        d = pd.Timestamp(when.date())
        t = when.hour + when.minute / 60
        i = days.searchsorted(d)  # first trading day >= d
        if t == 0:      # time unknown: could hit day d or the next one
            cands = [i, i + 1]
        elif t >= 16:   # after the close -> next session gaps
            cands = [i + 1 if i < len(days) and days[i] == d else i]
        else:           # before/during the session -> that session
            cands = [i]
        for j in cands:
            if 0 <= j < len(days):
                keys.add((sym, days[j]))
    covered = ed.groupby("sym")["when"].min()
    stocks["earn"] = pd.Series([(s, d) in keys for s, d in zip(stocks["sym"], stocks["date"])], index=stocks.index, dtype=object)
    # only trust the flag where we have earnings history for that symbol and period
    first = stocks["sym"].map(covered)
    stocks.loc[first.isna() | (stocks["date"] < first.dt.normalize()), "earn"] = np.nan
    return stocks, True


def clustered(df, col):
    """mean, date-clustered SE, median, hit rate, n."""
    x = df[col].dropna()
    if len(x) < 30:
        return None
    d = df.loc[x.index, "date"]
    e = x - x.mean()
    se = np.sqrt((e.groupby(d).sum() ** 2).sum()) / len(x)
    return {"mean": x.mean() * 100, "se": se * 100, "t": x.mean() / se if se else 0,
            "median": x.median() * 100, "hit": (x > 0).mean() * 100, "n": int(len(x)),
            "days": int(d.nunique())}


def summarize(df, label):
    """h* = vs SPY; d* = vs SPY and vs the stock's own average (removes the
    survivorship drift of today's index members)."""
    out = {"label": label, "n": int(len(df))}
    for h in HORIZONS:
        out[f"h{h}"] = clustered(df, f"x{h}")
        out[f"d{h}"] = clustered(df, f"dm{h}")
    for k in CLOSE_H:
        out[f"c{k}"] = clustered(df, f"cm{k}")
    out["path"] = [df[f"x{k}"].mean() * 100 for k in range(PATH_DAYS)]
    out["dpath"] = [df[f"dm{k}"].mean() * 100 for k in range(PATH_DAYS)]
    out["fill"] = float(df["filled"].mean() * 100) if len(df) else None
    return out


def main():
    px = add_features(load())
    stocks = market_adjust(px)
    stocks, have_earn = add_earnings(stocks)
    base = stocks[(stocks.date >= START) & (stocks.prev_close >= MIN_PRICE) & (stocks.adv >= MIN_ADV)
                  & stocks.gap.notna() & (stocks.gap.abs() <= MAX_ABS_GAP)].copy()
    # each stock's own average excess return over every eligible day, per horizon
    for k in range(PATH_DAYS):
        base[f"dm{k}"] = base[f"x{k}"] - base.groupby("sym")[f"x{k}"].transform("mean")
    for k in CLOSE_H:
        base[f"cm{k}"] = base[f"y{k}"] - base.groupby("sym")[f"y{k}"].transform("mean")

    res = {"params": {"start": START, "end": str(base.date.max().date()), "symbols": int(base.sym.nunique()),
                      "stock_days": int(len(base)), "cost_bps": COST_BPS, "min_price": MIN_PRICE,
                      "min_adv": MIN_ADV, "horizons": HORIZONS, "have_earnings": have_earn},
           "buckets": [], "splits": {}, "periods": [], "corr": {}}

    nogap = base[base.gap.abs() < 0.005]
    res["baseline"] = summarize(nogap, "No gap (within ±0.5%)")
    for lo, hi, name in UP_BUCKETS + DN_BUCKETS:
        res["buckets"].append(summarize(base[(base.gap >= lo) & (base.gap < hi)], name))

    up = base[base.gap >= 0.04]
    dn = base[base.gap <= -0.04]
    for side, d in (("up", up), ("down", dn)):
        s = {}
        s["rvol"] = [summarize(d[d.rvol < 1.5], "Volume < 1.5× normal"),
                     summarize(d[(d.rvol >= 1.5) & (d.rvol < 3)], "1.5–3×"),
                     summarize(d[d.rvol >= 3], "3× or more")]
        if have_earn:
            s["earn"] = [summarize(d[d.earn == True], "Earnings gap"),  # noqa: E712
                         summarize(d[d.earn == False], "No earnings")]  # noqa: E712
        s["regime"] = [summarize(d[d.bull == True], "SPY above 200-day"),  # noqa: E712
                       summarize(d[d.bull == False], "SPY below 200-day")]  # noqa: E712
        s["market"] = [summarize(d[d.spy_gap.abs() < 0.005], "Market flat at open"),
                       summarize(d[d.spy_gap.abs() >= 0.005], "Market also gapped")]
        res["splits"][side] = s

    # robustness: first half vs second half of the decade
    mid = pd.Timestamp("2021-01-01")
    for name, d in (("2016–2020", up[up.date < mid]), ("2021–now", up[up.date >= mid])):
        res["periods"].append(summarize(d, "Up ≥4% · " + name))
    for name, d in (("2016–2020", dn[dn.date < mid]), ("2021–now", dn[dn.date >= mid])):
        res["periods"].append(summarize(d, "Down ≥4% · " + name))

    # does gap size line up with same-day continuation? (rank correlation, |gap| >= 2%)
    big = base[base.gap.abs() >= 0.02]
    for h in HORIZONS:
        res["corr"][f"h{h}"] = float(big["gap"].corr(big[f"x{h}"], method="spearman"))

    (DATA / "results.json").write_text(json.dumps(res, indent=1, default=float))
    p = res["params"]
    print(f"{p['symbols']} symbols, {p['stock_days']:,} stock-days, {p['start']}..{p['end']}, earnings={have_earn}")
    print(f"{'bucket':<22}{'n':>7}  " + "  ".join(f"{'d' + str(h + 1):>15}" for h in HORIZONS) + "   fill%")
    for key, title in (("h", "vs SPY"), ("d", "vs SPY and the stock's own average")):
        print("--", title)
        for b in [res["baseline"]] + res["buckets"]:
            cells = [f"{b[f'{key}{h}']['mean']:+.2f} (t{b[f'{key}{h}']['t']:+.1f})" if b[f"{key}{h}"] else "-" for h in HORIZONS]
            print(f"{b['label']:<22}{b['n']:>7}  " + "  ".join(f"{c:>15}" for c in cells) + f"   {b['fill']:.0f}")
    print("spearman(gap, excess):", {k: round(v, 3) for k, v in res["corr"].items()})


if __name__ == "__main__":
    main()
