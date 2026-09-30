"""Daily sync: scan wstradesws@gmail.com for new Wealthsimple notification
emails, parse them, and append any new transactions to the 'Untitled' tab of
the WS Transactions Log Google Sheet.

Idempotent: tracks processed Gmail message IDs in sync_state.json so re-runs
(or overlapping date-boundary re-scans) never produce duplicate rows.
"""
import json
import os
import re
from datetime import datetime, timezone

import gmail_client
import sheets_client
from ws_parser import parse

STATE_PATH = os.path.join(os.path.dirname(__file__), "sync_state.json")
SHEET_NAME = "Untitled"


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"processed_ids": [], "last_checked_date": None}


def _save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _parse_dt(record):
    m = re.search(r"[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} [\d:]+", record.get("date", ""))
    if not m:
        return datetime.min
    try:
        return datetime.strptime(m.group(0), "%a, %d %b %Y %H:%M:%S")
    except Exception:
        return datetime.min


def _to_row(r):
    t = r["transaction_type"]
    dt = _parse_dt(r)
    holdings_impact = t in ("stock_fill", "option_fill", "option_exercise_assignment", "multi_leg_option_fill")

    if t == "multi_leg_option_fill":
        leg_strs = [f"{l['action']} to {l['open_close']} {l['quantity']}x {l['underlying']} "
                    f"${l['strike']:g} {l['option_type']} exp {l['expiry']}" for l in r.get("legs", [])]
        detail = f"{r.get('strategy')}: " + "; ".join(leg_strs)
        amount = r.get("net_cash")
    elif t == "stock_fill":
        detail = (f"{r.get('action')} {r.get('shares_filled')} {r.get('symbol')} "
                   f"@ {r.get('avg_price')} {r.get('avg_price_currency')}")
        if r.get("partial"):
            detail += " (PARTIAL)"
        amount = r.get("total_value")
    elif t == "option_fill":
        detail = (f"{r.get('action')} to {r.get('open_close')} {r.get('contracts')}x "
                   f"{r.get('raw_option_desc')} exp {r.get('expiry')} "
                   f"@ {r.get('avg_price')} {r.get('avg_price_currency')}")
        amount = r.get("total_value")
    elif t == "option_exercise_assignment":
        detail = (f"{r.get('outcome')}: {r.get('share_action')} {r.get('shares')} "
                   f"{r.get('underlying')} (from {r.get('contracts')}x "
                   f"${r.get('strike')} {r.get('option_type')})")
        amount = r.get("total_cost")
    elif t == "order_cancelled":
        detail = f"Cancelled: {r.get('order_type')} {r.get('raw_option_desc') or r.get('symbol')}"
        amount = None
    elif t == "dividend":
        detail = f"Dividend from {r.get('symbol')}"
        amount = r.get("amount")
    elif t == "internal_transfer":
        detail = f"Transfer {r.get('from_account')} -> {r.get('to_account')}"
        amount = r.get("amount")
    elif t == "fx_conversion":
        detail = (f"FX: {r.get('amount_submitted')} {r.get('submitted_currency')} "
                   f"-> {r.get('amount_received')} {r.get('received_currency')}")
        amount = r.get("amount_received")
    elif t == "direct_deposit":
        detail = f"Deposit from {r.get('from_source')}"
        amount = r.get("amount")
    elif t == "wire_transfer":
        detail = f"Wire from {r.get('from_source')} -> {r.get('to_account')}"
        amount = r.get("amount")
    else:
        return None  # ignored / unrecognized - not logged

    return [
        dt.strftime("%Y-%m-%d %H:%M") if dt != datetime.min else "",
        t,
        r.get("account") or r.get("to_account") or "",
        "Y" if holdings_impact else "",
        detail,
        amount,
    ]


def run(dry_run=False):
    state = _load_state()
    processed = set(state["processed_ids"])

    query = f"after:{state['last_checked_date']}" if state["last_checked_date"] else ""
    messages = gmail_client.list_messages(query, max_results=100)

    new_rows = []
    newly_processed = []
    skipped_ignored = 0
    latest_date = state["last_checked_date"]

    for m in messages:
        msg_id = m["id"]
        if msg_id in processed:
            continue
        full = gmail_client.get_message(msg_id)
        body = gmail_client.extract_body(full)
        subject = gmail_client.header(full, "Subject")
        date_hdr = gmail_client.header(full, "Date")

        text = f"Subject: {subject}\nDate: {date_hdr}\n\n{body}"
        parsed = parse(text)
        newly_processed.append(msg_id)

        row = _to_row(parsed)
        if row:
            new_rows.append(row)
        else:
            skipped_ignored += 1

        try:
            d = datetime.strptime(date_hdr[:16], "%a, %d %b %Y")
            iso = d.strftime("%Y/%m/%d")
            if latest_date is None or iso > latest_date:
                latest_date = iso
        except Exception:
            pass

    if new_rows and not dry_run:
        sheets_client.append_rows(SHEET_NAME, new_rows)

    state["processed_ids"] = list(processed | set(newly_processed))
    if latest_date:
        state["last_checked_date"] = latest_date
    if not dry_run:
        _save_state(state)

    print(f"Checked {len(messages)} message(s), {len(newly_processed)} new, "
          f"{len(new_rows)} logged as transactions, {skipped_ignored} ignored/non-transactional.")
    return new_rows


if __name__ == "__main__":
    run()
