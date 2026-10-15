"""Pull IBKR statements through the Flex Web Service (read-only) and write the
Positions, Trades and Account tabs of "Tim's Desk Prices".

Flex Web Service = two HTTP calls per query:
  SendRequest?t=TOKEN&q=QUERY_ID&v=3  -> ReferenceCode + GetStatement URL
  GetStatement?t=TOKEN&q=REF&v=3      -> the statement XML, or "still
                                          generating" (code 1019) -> retry
Secrets (GitHub Actions, set by Tim): IBKR_FLEX_TOKEN, IBKR_FLEX_ACTIVITY_ID,
IBKR_FLEX_TRADES_ID. The token is never printed; URLs carrying it are never
logged. This job cannot place orders: the Flex service is reporting-only.

Token expiry: optional repo variable IBKR_TOKEN_EXPIRES (YYYY-MM-DD). When it
is <= 14 days away the Account tab shows a warning and the workflow opens a
GitHub issue (GitHub emails Tim).
"""
import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
UA = "tims-desk/1.0 (Python urllib)"  # IBKR rejects requests without a User-Agent
RETRY_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
EXPIRY_WARN_DAYS = 14

# Preferred column order; any other attribute IBKR sends is appended after these.
POS_COLS = ["accountId", "assetCategory", "symbol", "description", "underlyingSymbol", "putCall", "strike",
            "expiry", "multiplier", "position", "markPrice", "positionValue", "currency", "fxRateToBase",
            "costBasisPrice", "costBasisMoney", "fifoPnlUnrealized", "percentOfNAV", "side", "reportDate"]
TRADE_COLS = ["tradeID", "accountId", "dateTime", "tradeDate", "assetCategory", "symbol", "description",
              "underlyingSymbol", "putCall", "strike", "expiry", "multiplier", "buySell", "openCloseIndicator",
              "quantity", "tradePrice", "price", "proceeds", "amount", "ibCommission", "commission", "netCash",
              "currency", "fifoPnlRealized", "orderType", "exchange"]
# Kept out of the sheet: noisy or identifying beyond what the dashboard needs.
DROP = {"acctAlias", "model", "isin", "cusip", "figi", "securityID", "securityIDType", "conid",
        "underlyingConid", "underlyingSecurityID", "underlyingListingExchange", "issuer", "issuerCountryCode",
        "listingExchange", "principalAdjustFactor", "serialNumber", "deliveryType", "commodityType",
        "fineness", "weight", "ibOrderID", "ibExecID", "brokerageOrderID", "orderReference", "volatilityOrderLink",
        "exchOrderId", "extExecID", "traderID", "origTradePrice", "origTradeDate", "origTradeID", "origOrderID",
        "origTransactionID", "relatedTradeID", "relatedTransactionID", "rtn", "clearingFirmID", "transactionID"}


class FlexError(Exception):
    pass


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _status(root):
    return (root.findtext("Status") or "", root.findtext("ErrorCode") or "", root.findtext("ErrorMessage") or "")


def fetch_statement(token: str, query_id: str, label: str) -> ET.Element:
    """SendRequest, then poll GetStatement with backoff. Returns the
    FlexQueryResponse root. Never logs the token."""
    q = urllib.parse.urlencode
    for attempt in range(4):
        root = ET.fromstring(_get(f"{BASE}/SendRequest?{q({'t': token, 'q': query_id, 'v': 3})}"))
        st, code, msg = _status(root)
        if st == "Success":
            break
        if code in RETRY_CODES and attempt < 3:
            print(f"{label}: SendRequest {code} {msg} - retrying")
            time.sleep(10 * (attempt + 1))
            continue
        raise FlexError(f"{label}: SendRequest failed: {code} {msg}")
    ref = root.findtext("ReferenceCode")
    get_url = (root.findtext("Url") or f"{BASE}/GetStatement").strip()

    wait, waited = 5, 0
    while True:
        time.sleep(wait)
        waited += wait
        body = _get(f"{get_url}?{q({'t': token, 'q': ref, 'v': 3})}")
        root = ET.fromstring(body)
        if root.tag == "FlexQueryResponse":
            return root
        st, code, msg = _status(root)
        if code in RETRY_CODES and waited < 300:
            print(f"{label}: statement not ready ({code}), waiting {min(wait * 2, 60)}s")
            wait = min(wait * 2, 60)
            continue
        raise FlexError(f"{label}: GetStatement failed: {code} {msg}")


def _rows(elems, preferred):
    elems = list(elems)
    cols = [c for c in preferred if any(c in e.attrib for e in elems)]
    for e in elems:
        for k in e.attrib:
            if k not in cols and k not in DROP:
                cols.append(k)
    return cols, [[e.attrib.get(c, "") for c in cols] for e in elems]


def parse_activity(root):
    """-> (positions_elems, trade_elems, account_summary dict)"""
    positions, trades, summary = [], [], {}
    for st in root.iter("FlexStatement"):
        acct = st.get("accountId", "")
        # Open positions: keep summary rows (one per holding), not individual lots.
        for p in st.iter("OpenPosition"):
            if p.get("levelOfDetail", "SUMMARY").upper() in ("SUMMARY", ""):
                positions.append(p)
        trades += [t for t in st.iter("Trade") if t.get("levelOfDetail", "EXECUTION").upper() == "EXECUTION"]
        for n in st.iter("ChangeInNAV"):
            summary[acct] = {
                "from": n.get("fromDate", ""), "to": n.get("toDate", ""),
                "start": n.get("startingValue", ""), "end": n.get("endingValue", ""),
                "mtm": n.get("mtm", ""), "realized": n.get("realized", ""),
                "deposits": n.get("depositsWithdrawals", ""), "dividends": n.get("dividends", ""),
                "interest": n.get("interest", ""), "commissions": n.get("commissions", ""),
                "currency": n.get("currency", st.get("currency", "")),
            }
        for c in st.iter("CashReportCurrency"):
            if c.get("currency") == "BASE_SUMMARY" or c.get("levelOfDetail", "").upper() == "BASECURRENCY":
                summary.setdefault(acct, {})["cash"] = c.get("endingCash", "")
    return positions, trades, summary


def merge_trades(existing: list[list[str]], cols: list[str], new_rows: list[list[str]]):
    """Keep every trade ever seen (keyed by tradeID, else a composite key),
    newest first. `existing` is the current Trades tab incl. header."""
    def key(row, header):
        r = dict(zip(header, row))
        return r.get("tradeID") or "|".join(r.get(k, "") for k in ("dateTime", "symbol", "buySell", "quantity", "tradePrice", "price"))

    out = {}
    if existing:
        old_header = existing[0]
        for c in old_header:
            if c and c not in cols:
                cols.append(c)
        for row in existing[1:]:
            if any(row):
                d = dict(zip(old_header, row))
                out[key(row, old_header)] = [d.get(c, "") for c in cols]
    for row in new_rows:
        d = dict(zip(cols, row))
        out[key(row, cols)] = [d.get(c, "") for c in cols]
    t_idx = cols.index("dateTime") if "dateTime" in cols else (cols.index("tradeDate") if "tradeDate" in cols else 0)
    rows = sorted(out.values(), key=lambda r: r[t_idx] if t_idx < len(r) else "", reverse=True)
    return [cols] + rows


def expiry_warning(today: date):
    raw = os.environ.get("IBKR_TOKEN_EXPIRES", "").strip()
    if not raw:
        return None, None
    try:
        exp = date.fromisoformat(raw)
    except ValueError:
        return None, f"IBKR_TOKEN_EXPIRES '{raw}' is not YYYY-MM-DD"
    days = (exp - today).days
    if days <= EXPIRY_WARN_DAYS:
        return days, f"IBKR Flex token expires {exp.isoformat()} ({days} days). Renew it in Client Portal and update the IBKR_FLEX_TOKEN secret and IBKR_TOKEN_EXPIRES variable."
    return days, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print a summary, don't write the sheet")
    args = ap.parse_args()

    token = os.environ.get("IBKR_FLEX_TOKEN", "").strip()
    activity_id = os.environ.get("IBKR_FLEX_ACTIVITY_ID", "").strip()
    trades_id = os.environ.get("IBKR_FLEX_TRADES_ID", "").strip()
    if not token or not activity_id:
        print("IBKR secrets not set yet (IBKR_FLEX_TOKEN / IBKR_FLEX_ACTIVITY_ID) - nothing to do.")
        return

    now = datetime.now(timezone.utc)
    as_of = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    days_left, warn = expiry_warning(now.date())

    act = fetch_statement(token, activity_id, "activity")
    positions, trades, summary = parse_activity(act)
    confirms = []
    if trades_id:
        try:
            conf = fetch_statement(token, trades_id, "trade-confirms")
            confirms = list(conf.iter("TradeConfirm"))
        except FlexError as e:  # confirms are a bonus; the activity statement already has trades
            print(f"warning: {e}")

    pcols, prows = _rows(positions, POS_COLS)
    pos_tab = [pcols + ["as_of"]] + [r + [as_of] for r in prows] if pcols else [["symbol", "as_of"]]

    tcols, trows = _rows(trades + confirms, TRADE_COLS)
    acct_rows = [["account", "currency", "nav", "nav_start", "change", "change_pct", "mtm", "realized",
                  "deposits", "dividends", "interest", "commissions", "cash", "period_from", "period_to",
                  "as_of", "token_days_left", "warning"]]
    for acct, s in summary.items():
        try:
            chg = float(s["end"]) - float(s["start"]) - float(s.get("deposits") or 0)
            pct = round(chg / float(s["start"]) * 100, 3) if float(s["start"]) else ""
            chg = round(chg, 2)
        except (KeyError, ValueError):
            chg, pct = "", ""
        acct_rows.append([acct, s.get("currency", ""), s.get("end", ""), s.get("start", ""), chg, pct,
                          s.get("mtm", ""), s.get("realized", ""), s.get("deposits", ""), s.get("dividends", ""),
                          s.get("interest", ""), s.get("commissions", ""), s.get("cash", ""), s.get("from", ""),
                          s.get("to", ""), as_of, days_left if days_left is not None else "", warn or ""])
    if len(acct_rows) == 1:
        acct_rows.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", as_of,
                          days_left if days_left is not None else "", warn or ""])

    print(f"positions={len(prows)} trades(new)={len(trows)} accounts={len(summary)}"
          + (f" token_days_left={days_left}" if days_left is not None else ""))
    if warn:
        print(f"::warning::{warn}")
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(f"expiry_warning={warn}\n")

    if args.dry_run:
        print("positions columns:", pcols)
        print("trade columns:", tcols)
        return

    import sheets_writer as sw
    try:
        existing = sw.get_values("Trades!A1:ZZ")
    except Exception:
        existing = []
    trades_tab = merge_trades(existing, tcols or ["tradeID"], trows)

    sw.write_table("Positions", pos_tab, raw=True)
    sw.write_table("Trades", trades_tab, raw=True)
    sw.write_table("Account", acct_rows, raw=True)
    print(f"wrote Positions {len(pos_tab)-1}, Trades {len(trades_tab)-1}, Account {len(acct_rows)-1}")


if __name__ == "__main__":
    try:
        main()
    except FlexError as e:
        print(f"::error::{e}")
        sys.exit(1)
