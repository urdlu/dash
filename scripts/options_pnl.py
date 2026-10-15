"""Options journey: net P&L of every options trade Tim has made at Wealthsimple.

Sources (both in the "WS Transactions Log" spreadsheet):
  * the Wealthsimple activities export tab ("activities-export-...") - complete,
    exact contract symbols, up to its "As of" timestamp;
  * the trade-email log tab ("Untitled", filled daily by the Apps Script WS
    sync) - everything after that timestamp.

Every fill becomes an event with legs (contract, signed qty) and a USD cash
flow. Events that touch the same contract are chained into one TRADE (so a
roll, a spread or an open+close pair is one trade). A trade's P&L is the sum
of its cash flows plus the current value of any legs still open:

  * open legs are marked at the Yahoo option-chain mid (bid/ask), falling back
    to last price, then to intrinsic value (flagged "est.");
  * legs past expiry with no closing fill expired worthless;
  * an assignment closes the short leg at its intrinsic value on that day
    (the loss shows here, the shares go to the stock side).

Fees aren't in the emails, so P&L is before fees (Wealthsimple options fees
are small per contract). Amounts are USD.

Writes two tabs to "Tim's Desk Prices":
  OptionsSummary  metric,value rows (net, realized, unrealized, win rate...)
  OptionTrades    one row per trade, newest first

Run:  python scripts/options_pnl.py               (reads the sheet, writes tabs)
      python scripts/options_pnl.py --csv-dir .work --dry-run   (local test)
"""
import argparse
import csv
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WS_LOG_ID = "15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw"  # "WS Transactions Log"
PT = ZoneInfo("America/Vancouver")
OCC = re.compile(r"^\s*([A-Z.]+)\s+(\d{2})(\d{2})(\d{2})([CP])(\d{8})\s*$")
SINGLE = re.compile(r"(BUY|SELL) to (OPEN|CLOSE) (\d+)x (\S+) \$?([\d.]+) (call|put) exp (\d{4}-\d{2}-\d{2})(?: @ ([\d.]+))?")
ASSIGN = re.compile(r"Assigned: (BOUGHT|SOLD) ([\d.]+) (\S+) \(from (\d+)x \$([\d.]+) (put|call)\)")


def num(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------- loading
def load_from_sheet():
    import sheets_writer as sw
    svc = sw._svc()
    meta = svc.spreadsheets().get(spreadsheetId=WS_LOG_ID).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    export_tab = next((t for t in titles if t.lower().startswith("activities")), None)

    def vals(rng):
        return svc.spreadsheets().values().get(spreadsheetId=WS_LOG_ID, range=rng).execute().get("values", [])
    log = vals("'Untitled'!A1:F")
    export = vals(f"'{export_tab}'!A1:O") if export_tab else []
    return export, log


def load_from_csv(d):
    d = Path(d)
    rd = lambda f: list(csv.reader(open(d / f, encoding="utf-8"))) if (d / f).exists() else []
    return rd("wsexport.csv"), rd("wslog.csv")


# ---------------------------------------------------------------- events
def export_events(rows):
    """-> (events, cutoff_utc). Export amounts are in the row's currency; CAD
    rows are converted with the FX rate printed in the description."""
    if not rows:
        return [], None
    h = rows[0]
    ix = {k: h.index(k) for k in h}
    events, cutoff = [], None
    for r in rows[1:]:
        r = r + [""] * (len(h) - len(r))
        m = re.search(r"As of (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) GMT([+-]\d{2}):(\d{2})", " ".join(r))
        if m:
            off = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)) * (1 if m.group(2)[0] == "+" else -1))
            cutoff = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone(off)).astimezone(timezone.utc)
            continue
        o = OCC.match(r[ix["symbol"]] or "")
        if not o:
            continue
        und, yy, mm, dd, cp, k = o.groups()
        key = (und, f"20{yy}-{mm}-{dd}", "call" if cp == "C" else "put", int(k) / 1000)
        when = datetime.fromisoformat(r[ix["effective_at"]]).astimezone(timezone.utc)
        qty = num(r[ix["quantity"]]) or 0
        acct = r[ix["account_type"]]
        if r[ix["activity_type"]] == "OptionAssign":
            events.append({"t": when, "legs": [(key, qty)], "cash": 0.0, "assign": True, "acct": acct,
                           "text": f"Assigned {und} {key[3]:g} {key[2]}"})
            continue
        cash = num(r[ix["net_cash_amount"]]) or 0.0
        if r[ix["currency"]] == "CAD":
            fx = re.search(r"FX Rate: ([\d.]+)", r[ix["description"]])
            cash = cash / float(fx.group(1)) if fx else cash
        side = "BUY" if qty > 0 else "SELL"
        events.append({"t": when, "legs": [(key, qty)], "cash": cash, "acct": acct,
                       "text": f"{side} {abs(qty):g}x {und} {key[3]:g} {key[2]} exp {key[1]}"})
    return events, cutoff


def log_events(rows, cutoff):
    """Trade-email rows newer than the export. Log times are UTC."""
    events = []
    for r in rows[1:]:
        if len(r) < 6 or "option" not in r[1]:
            continue
        try:
            when = datetime.strptime(r[0].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if cutoff and when <= cutoff:
            continue
        typ, acct, detail, amount = r[1], r[2].strip(), r[4], num(r[5]) or 0.0
        if typ == "option_exercise_assignment":
            a = ASSIGN.search(detail)
            if a:
                events.append({"t": when, "assign_text": a.groups(), "legs": [], "cash": 0.0, "assign": True,
                               "acct": acct, "text": detail})
            continue
        legs = []
        for side, _oc, n, und, k, cp, exp, _px in SINGLE.findall(detail):
            legs.append(((und, exp, cp, float(k)), (1 if side == "BUY" else -1) * int(n)))
        if not legs:
            continue
        if typ == "multi_leg_option_fill":
            cash = amount  # signed net: credit > 0, debit < 0
        else:
            cash = amount if legs[0][1] < 0 else -amount
        events.append({"t": when, "legs": legs, "cash": cash, "acct": acct, "text": detail})
    return events


def resolve_email_assignments(events):
    """An emailed assignment names underlying/strike/type but not expiry:
    close the matching open short with the earliest expiry."""
    net = defaultdict(float)
    for e in sorted(events, key=lambda e: e["t"]):
        if "assign_text" in e:
            verb, _shares, und, n, k, cp = e["assign_text"]
            cands = sorted((key for key, q in net.items()
                            if q < 0 and key[0] == und and key[2] == cp and abs(key[3] - float(k)) < 1e-6),
                           key=lambda key: key[1])  # earliest expiry first
            if cands:
                e["legs"] = [(cands[0], int(n))]
        for key, q in e["legs"]:
            net[key] += q
    return [e for e in events if e["legs"]]


# ---------------------------------------------------------------- grouping
def group_trades(events):
    parent = list(range(len(events)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first = {}
    for i, e in enumerate(events):
        for key, _ in e["legs"]:
            if key in first:
                parent[find(i)] = find(first[key])
            else:
                first[key] = i
    groups = defaultdict(list)
    for i, e in enumerate(events):
        groups[find(i)].append(e)
    return [sorted(g, key=lambda e: e["t"]) for g in groups.values()]


# ---------------------------------------------------------------- marks
class Marks:
    """Yahoo option-chain marks, cached per underlying+expiry."""
    def __init__(self, offline=False):
        self.offline, self.chains, self.spot, self.hist = offline, {}, {}, {}

    def _yf(self):
        import yfinance as yf
        return yf

    def underlying(self, und):
        if und not in self.spot:
            p = None
            if not self.offline:
                try:
                    p = float(self._yf().Ticker(und).fast_info["last_price"])
                except Exception:
                    p = None
            self.spot[und] = p
        return self.spot[und]

    def close_on(self, und, d):
        """Underlying close on (or just before) date d - for assignments."""
        if self.offline:
            return None
        if und not in self.hist:
            try:
                self.hist[und] = self._yf().Ticker(und).history(period="2y", auto_adjust=False)["Close"]
            except Exception:
                self.hist[und] = None
        h = self.hist[und]
        if h is None or h.empty:
            return None
        h = h[h.index.date <= d]
        return float(h.iloc[-1]) if len(h) else None

    def option(self, key):
        """-> (price per share, how) for one contract."""
        und, exp, cp, k = key
        spot = self.underlying(und)
        intrinsic = max(0.0, (spot - k) if cp == "call" else (k - spot)) if spot else None
        if not self.offline:
            ck = (und, exp)
            if ck not in self.chains:
                try:
                    ch = self._yf().Ticker(und).option_chain(exp)
                    self.chains[ck] = {"call": ch.calls, "put": ch.puts}
                except Exception:
                    self.chains[ck] = None
            ch = self.chains[ck]
            if ch is not None:
                df = ch[cp]
                row = df[(df["strike"] - k).abs() < 1e-6]
                if len(row):
                    r = row.iloc[0]
                    bid, ask, last = (float(r.get(c) or 0) for c in ("bid", "ask", "lastPrice"))
                    if bid > 0 and ask > 0:
                        return (bid + ask) / 2, "mid"
                    if last > 0:
                        return last, "last"
        if intrinsic is not None:
            return intrinsic, "est."
        return None, "none"


# ---------------------------------------------------------------- P&L
def describe(trade):
    e0 = trade[0]
    legs = [l for e in trade for l in e["legs"]]
    unds = sorted({k[0] for k, _ in legs})
    opens = e0["legs"]
    if "Long call condor" in e0["text"] or "condor" in e0["text"].lower():
        name = "Call condor"
    elif "credit spread" in e0["text"].lower():
        name = "Put credit spread" if opens[0][0][2] == "put" else "Call credit spread"
    elif len(opens) == 1:
        (und, exp, cp, k), q = opens[0]
        far = (date.fromisoformat(exp) - e0["t"].date()).days > 270
        name = ("LEAPS " if far and q > 0 else "Long " if q > 0 else "Short ") + cp
    else:
        name = "Multi-leg"
    if any("Rolling" in e["text"] for e in trade):
        name += " (rolled)"
    kind = "income" if all(q < 0 for _, q in opens) else "long" if all(q > 0 for _, q in opens) else "spread"
    return "/".join(unds), name, kind


def compute(events, marks, today):
    trades = group_trades(events)
    out = []
    for tr in trades:
        net = defaultdict(float)
        cash = 0.0
        assigned = False
        for e in tr:
            cash += e["cash"]
            for key, q in e["legs"]:
                net[key] += q
            if e.get("assign"):
                assigned = True
                for key, q in e["legs"]:
                    # closed at intrinsic on the assignment day: a short put
                    # assigned when the stock is below the strike loses (K - S)
                    s = marks.close_on(key[0], e["t"].astimezone(PT).date())
                    if s is not None:
                        intr = max(0.0, (key[3] - s) if key[2] == "put" else (s - key[3]))
                        cash -= intr * 100 * abs(q)
                        e["intrinsic"] = intr
        open_value, notes, open_legs, expired, watch = 0.0, [], [], False, []
        for key, q in net.items():
            if abs(q) < 1e-9:
                continue
            if date.fromisoformat(key[1]) < today:
                expired = True
                continue
            px, how = marks.option(key)
            open_legs.append(f"{q:+g} {key[0]} {key[3]:g}{key[2][0].upper()} {key[1]}")
            spot = marks.underlying(key[0])
            dte = (date.fromisoformat(key[1]) - today).days
            if q < 0 and spot:
                # % the stock can still move before the short strike is in the money
                room = (spot - key[3]) / spot * 100 if key[2] == "put" else (key[3] - spot) / spot * 100
                watch.append(f"{key[0]} {key[3]:g}{key[2][0].upper()} {key[1]}|{room:.1f}|{dte}")
            if px is None:
                notes.append(f"no price for {key[0]} {key[3]:g}{key[2][0].upper()} {key[1]}")
                continue
            open_value += q * px * 100
            if how != "mid":
                notes.append(f"{key[0]} {key[3]:g}{key[2][0].upper()} {key[1]} {how}")
        und, name, kind = describe(tr)
        status = "open" if open_legs else "assigned" if assigned else "expired" if expired else "closed"
        last_exp = max(date.fromisoformat(k[1]) for k in net)
        closed_on = "" if open_legs else (min(last_exp, today) if expired and not assigned else tr[-1]["t"].astimezone(PT).date())
        out.append({
            "opened": tr[0]["t"].astimezone(PT).date().isoformat(), "closed": str(closed_on) if closed_on else "",
            "underlying": und, "strategy": name, "kind": kind, "status": status,
            "account": tr[0]["acct"], "fills": len(tr), "cash": round(cash, 2), "open_value": round(open_value, 2),
            "pnl": round(cash + open_value, 2), "open_legs": "; ".join(open_legs), "marks": "; ".join(notes),
            "short_legs": "; ".join(watch),
            "premium_sold": round(sum(e["cash"] for e in tr if e["cash"] > 0), 2),
            "premium_bought": round(-sum(e["cash"] for e in tr if e["cash"] < 0), 2),
        })
    out.sort(key=lambda r: (r["closed"] or "9999", r["opened"]), reverse=True)
    return out


def summarize(trades, as_of):
    closed = [t for t in trades if t["status"] != "open"]
    opened = [t for t in trades if t["status"] == "open"]
    wins = [t for t in closed if t["pnl"] > 0]
    by_kind = lambda k, rows: round(sum(t["pnl"] for t in rows if t["kind"] == k), 2)
    rows = [
        ("net_pnl", round(sum(t["pnl"] for t in trades), 2)),
        ("realized_pnl", round(sum(t["pnl"] for t in closed), 2)),
        ("unrealized_pnl", round(sum(t["pnl"] for t in opened), 2)),
        ("open_value", round(sum(t["open_value"] for t in opened), 2)),
        ("premium_sold", round(sum(t["premium_sold"] for t in trades), 2)),
        ("premium_bought", round(sum(t["premium_bought"] for t in trades), 2)),
        ("trades_closed", len(closed)),
        ("trades_open", len(opened)),
        ("wins", len(wins)),
        ("losses", len(closed) - len(wins)),
        ("win_rate_pct", round(len(wins) / len(closed) * 100, 1) if closed else ""),
        ("income_pnl", by_kind("income", trades)),
        ("spread_pnl", by_kind("spread", trades)),
        ("long_pnl", by_kind("long", trades)),
        ("first_trade", min((t["opened"] for t in trades), default="")),
        ("currency", "USD"),
        ("as_of", as_of),
        ("note", "Before fees. Open positions at Yahoo option mid prices; assignments at intrinsic value."),
    ]
    return [["metric", "value"]] + [[k, v] for k, v in rows]


TRADE_COLS = ["opened", "closed", "underlying", "strategy", "kind", "status", "account", "fills",
              "cash", "open_value", "pnl", "premium_sold", "premium_bought", "open_legs", "marks",
              "short_legs"]  # "SPY 730P 2026-11-13|room %|days left" per open short leg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", help="read wsexport.csv / wslog.csv from here instead of the sheet")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true", help="no Yahoo calls (marks = none)")
    a = ap.parse_args()

    export, log = load_from_csv(a.csv_dir) if a.csv_dir else load_from_sheet()
    ev, cutoff = export_events(export)
    ev += log_events(log, cutoff)
    ev = resolve_email_assignments(ev)
    today = datetime.now(PT).date()
    trades = compute(ev, Marks(offline=a.offline), today)
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = summarize(trades, as_of)
    print(f"{len(ev)} fills -> {len(trades)} trades (export through {cutoff})")
    for k, v in summary[1:15]:
        print(f"  {k:<16} {v}")
    if a.dry_run:
        for t in trades:
            print(f"  {t['opened']} {t['closed'] or 'OPEN':<10} {t['underlying']:<5} {t['strategy']:<24} {t['status']:<8} "
                  f"cash {t['cash']:>10.2f}  open {t['open_value']:>10.2f}  pnl {t['pnl']:>10.2f}  {t['marks']}")
        return
    import sheets_writer as sw
    sw.write_table("OptionsSummary", summary, raw=True)
    sw.write_table("OptionTrades", [TRADE_COLS] + [[t[c] for c in TRADE_COLS] for t in trades], raw=True)
    print(f"wrote OptionsSummary, OptionTrades ({len(trades)} trades)")


if __name__ == "__main__":
    main()
