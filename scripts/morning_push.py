"""Morning push: one phone message before 6am PT with Tim's ETFs, IBKR account,
options P&L and yesterday's Wealthsimple trades, plus rule-based suggestions.
Deterministic, no LLM. Everything trade-related is labelled a suggestion.

Reads "Tim's Desk Prices" (service account) - tabs Current, History, Account,
OptionsSummary, OptionTrades - and "WS Transactions Log" for yesterday's trades.
Any missing piece is skipped, never fatal.

Env:
  NTFY_TOPIC     GitHub secret. Missing -> prints only.
  CRON_SCHEDULE  which cron fired; of the two UTC crons only the one matching
                 today's Vancouver offset sends, so it's 5:55am PT year-round.
  DRY_RUN=1      print instead of sending.
"""
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sheets_writer as sw

DASHBOARD_URL = "https://claude.ai/artifact/HUWMhdji1szrsFyCkpsfUz"
WS_LOG_ID = "15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw"
TZ = ZoneInfo("America/Vancouver")
CRON_FOR_OFFSET = {"55 12 * * 1-5": -7, "55 13 * * 1-5": -8}

BIG_MOVE = 1.5          # % one-day move worth a mention
NEAR_STRIKE = 3.0       # short option within this % of its strike
SHORT_DTE = 7           # short option expiring within this many days
DIST_GAP = 1.5          # yield points between latest-distribution and TTM yield


def should_run():
    sched = os.environ.get("CRON_SCHEDULE", "").strip()
    if sched not in CRON_FOR_OFFSET:
        return True  # manual run / local test
    offset_h = datetime.now(TZ).utcoffset().total_seconds() / 3600
    return offset_h == CRON_FOR_OFFSET[sched]


def num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def table(rows):
    if not rows:
        return []
    h = rows[0]
    return [dict(zip(h, r + [""] * (len(h) - len(r)))) for r in rows[1:]]


def tab(name):
    try:
        return table(sw.get_values(f"'{name}'!A1:ZZ"))
    except Exception as e:
        print(f"couldn't read {name}: {e.__class__.__name__}")
        return []


# ---------------------------------------------------------------- signals
def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(xs, n=14):
    if len(xs) < n + 1:
        return None
    d = [b - a for a, b in zip(xs, xs[1:])]
    ag = sum(max(x, 0) for x in d[:n]) / n
    al = sum(max(-x, 0) for x in d[:n]) / n
    for x in d[n:]:
        ag, al = (ag * (n - 1) + max(x, 0)) / n, (al * (n - 1) + max(-x, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def etf_flags(sym, closes, day):
    p, flags = closes[-1], []
    if day is not None and abs(day) >= BIG_MOVE:
        flags.append(f"{'up' if day > 0 else 'down'} {abs(day):.1f}% yesterday")
    r = rsi(closes)
    if r is not None and r <= 30:
        flags.append(f"RSI {r:.0f}, oversold: possible add")
    elif r is not None and r >= 70:
        flags.append(f"RSI {r:.0f}, overbought: possible trim")
    if len(closes) >= 51:
        s50, prev50 = sma(closes, 50), sum(closes[-51:-1]) / 50
        if closes[-2] < prev50 and p >= s50:
            flags.append("crossed above its 50-day average")
        elif closes[-2] > prev50 and p <= s50:
            flags.append("crossed below its 50-day average")
    last63 = closes[-63:]
    if len(last63) >= 20:
        if (max(last63) - p) / p * 100 <= 1:
            flags.append("at/near its 3-month high")
        elif (p - min(last63)) / p * 100 <= 1:
            flags.append("at/near its 3-month low")
    return flags


# ---------------------------------------------------------------- sections
def etf_section(ideas):
    current, history = tab("Current"), tab("History")
    if not current:
        return ["ETFs: no data"], None
    series = {}
    for row in reversed(history):  # History is newest-first
        for k, v in row.items():
            if k != "date" and num(v) is not None:
                series.setdefault(k.upper(), []).append(num(v))
    lines, groups, cur = [], [], []
    for r in current:
        if not r.get("symbol"):
            if cur:
                groups.append(cur)
            cur = []
            continue
        cur.append(r)
    if cur:
        groups.append(cur)
    as_of = max((r.get("as_of", "") for r in current), default="")
    for gi, rows in enumerate(groups):
        lines.append("Corporate class ETFs" if gi == 0 else "Income ETFs" if gi == 1 else f"Group {gi + 1}")
        for r in rows:
            sym, px, day = r["symbol"].upper(), num(r.get("price")), num(r.get("change_pct"))
            s = f"  {sym:<5} {px:>8.2f} {day:+5.1f}%" if px is not None and day is not None else f"  {sym:<5} —"
            yt, yd = num(r.get("yield_ttm")), num(r.get("yield_dist"))
            if yt is not None:
                s += f"  yld {yt:.1f}%"
                if yd is not None and abs(yd - yt) >= DIST_GAP:
                    ideas.append(f"{sym}: latest distribution implies {yd:.1f}% vs {yt:.1f}% TTM, "
                                 f"a {'cut' if yd < yt else 'raise'}? Worth checking.")
            lines.append(s)
            closes = series.get(sym, [])
            if len(closes) >= 2:
                ideas.extend(f"{sym}: {f}" for f in etf_flags(sym, closes, day))
    return lines, as_of


def portfolio_section():
    acct = [a for a in tab("Account") if a.get("account")]
    if not acct:
        return []
    cur = acct[0].get("currency", "")
    nav = sum(num(a.get("nav")) or 0 for a in acct)
    chg = sum(num(a.get("change")) or 0 for a in acct)
    pos = [p for p in tab("Positions") if p.get("symbol")]
    out = [f"IBKR: {nav:,.2f} {cur}  day {chg:+,.2f}  ·  {len(pos)} holding{'s' if len(pos) != 1 else ''}"]
    warn = next((a.get("warning") for a in acct if a.get("warning")), "")
    if warn:
        out.append("  ⚠ " + warn)
    return out


def options_section(ideas):
    s = {r["metric"]: r["value"] for r in tab("OptionsSummary") if r.get("metric")}
    if not s:
        return []
    net, real, unreal = num(s.get("net_pnl")), num(s.get("realized_pnl")), num(s.get("unrealized_pnl"))
    out = [f"Options (USD): net {net:+,.0f}  ·  realized {real:+,.0f}  ·  open {unreal:+,.0f}",
           f"  {s.get('trades_open', '?')} open trades  ·  win rate {s.get('win_rate_pct', '?')}% "
           f"of {s.get('trades_closed', '?')} closed"]
    for t in tab("OptionTrades"):
        if t.get("status") != "open":
            continue
        for leg in filter(None, (t.get("short_legs") or "").split("; ")):
            try:
                name, room, dte = leg.split("|")
                room, dte = float(room), int(dte)
            except ValueError:
                continue
            if room <= 0:
                ideas.append(f"{name}: short leg is in the money by {abs(room):.1f}%, {dte} days left. Review: roll, close or accept assignment.")
            elif room <= NEAR_STRIKE:
                ideas.append(f"{name}: {room:.1f}% from the short strike, {dte} days left. Worth watching.")
            elif dte <= SHORT_DTE:
                ideas.append(f"{name}: expires in {dte} days, {room:.1f}% out of the money. Let it expire or roll?")
    return out


def ws_section():
    try:
        rows = table(sw._svc().spreadsheets().values().get(
            spreadsheetId=WS_LOG_ID, range="'Untitled'!A1:F").execute().get("values", []))
    except Exception as e:
        print(f"couldn't read WS log: {e.__class__.__name__}")
        return []
    # log times are UTC; "since yesterday 6am PT" covers the last trading day
    since = (datetime.now(TZ) - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    if datetime.now(TZ).weekday() == 0:  # Monday: include Friday
        since -= timedelta(days=2)
    recent = []
    for r in rows:
        try:
            t = datetime.strptime(r["Date"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        typ = r.get("Type", "")
        if t >= since and ("fill" in typ or "assignment" in typ):  # trades only, not deposits/dividends
            recent.append(r)
    if not recent:
        return []
    out = [f"Wealthsimple: {len(recent)} trade{'s' if len(recent) != 1 else ''} since {since:%a}"]
    for r in recent[:5]:
        out.append(f"  {r.get('Detail', '')[:70]}")
    if len(recent) > 5:
        out.append(f"  …and {len(recent) - 5} more")
    return out


def build_message():
    ideas = []
    etf_lines, as_of = etf_section(ideas)
    opt = options_section(ideas)
    parts = []
    if ideas:
        parts.append("Suggestions (rules, not advice):\n" + "\n".join("• " + i for i in ideas[:10]))
    else:
        parts.append("Nothing flagged today.")
    for sec in (opt, portfolio_section(), ws_section(), etf_lines):
        if sec:
            parts.append("\n".join(sec))
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
        if age.total_seconds() > 40 * 3600:
            parts.append(f"⚠ Prices are {age.total_seconds() / 3600:.0f}h old. Check the Fetch prices workflow.")
    except (TypeError, ValueError):
        parts.append("⚠ Couldn't tell how old the prices are.")
    now = datetime.now(TZ)
    title = f"Tim's Desk · {now:%a %b} {now.day}" + (f" · {len(ideas)} to look at" if ideas else "")
    return title, "\n\n".join(parts), bool(ideas)


def send(title, body, has_ideas):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if os.environ.get("DRY_RUN") == "1" or not topic:
        print(f"[{'dry run' if topic else 'no NTFY_TOPIC'}] {title}\n{body}")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), method="POST",
        headers={"Title": title.encode("latin-1", "replace").decode("latin-1"), "Click": DASHBOARD_URL,
                 "Tags": "chart_with_upwards_trend" if has_ideas else "sunrise",
                 "Priority": "4" if has_ideas else "3"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"sent ({r.status}): {title}")


def main():
    if not should_run():
        print("Skipping: this cron is for the other DST offset.")
        return
    send(*build_message())


if __name__ == "__main__":
    main()
