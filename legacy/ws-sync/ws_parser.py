"""Parses Wealthsimple notification emails (plain-text bodies extracted from
forwarded .eml attachments) into normalized transaction records.

Covers every template seen in the sample batch:
  - order filled / partially filled (stock or option, buy or sell)
  - order cancelled
  - options exercised or assigned
  - dividend received
  - transfer between accounts
  - funds converted (FX)
  - direct deposit received
  - wire transfer received

Non-transactional emails (credit card statements/payments, fee schedule
updates, account agreement notices, options-expiring reminders) are
classified as "ignored" - they don't change holdings or cash.
"""
import re
from datetime import datetime


ALL_LABELS = [
    "Account", "Account nickname", "Type", "Option", "Symbol", "Contracts",
    "Shares", "Expiry", "Average price", "Total value", "Total cost", "Time",
    "Amount", "Amount submitted", "Amount received", "From", "To", "Status",
]


def _clean(text: str) -> str:
    text = (text.replace("͏", "").replace("‌", "")
            .replace("​", "").replace("�", "'"))
    return text.replace("*", "")  # strip bold-markdown markers some renderers add


def _lines(text: str):
    text = _clean(text)
    return [l.strip() for l in text.split("\n") if l.strip()]


def _field(lines, label):
    # Primary path: "Label: value" as its own line.
    for l in lines:
        if l.startswith(label + ":"):
            val = l.split(":", 1)[1].strip()
            if val:
                return val
    # Fallback: some renderers (e.g. Gmail's own "Forward") squash multiple
    # "Label:\nvalue" pairs onto shared lines with no separator beyond the
    # next label. Search the whole joined text and take everything up to
    # whichever known label comes next.
    joined = "\n".join(lines)
    other_labels = "|".join(re.escape(l) for l in ALL_LABELS if l != label)
    m = re.search(
        rf"{re.escape(label)}:\s*(.*?)(?:{other_labels}:|$)", joined, re.DOTALL
    )
    if m:
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        return val or None
    return None


def _money(s):
    if not s:
        return None, None
    m = re.search(r"(US)?\$?\s*([\d,]+\.?\d*)\s*(USD|CAD)?", s)
    if not m:
        return None, None
    amount = float(m.group(2).replace(",", ""))
    currency = m.group(3) or ("USD" if m.group(1) else "CAD")
    return currency, amount


def parse(text: str) -> dict | None:
    lines = _lines(text)
    joined = "\n".join(lines)

    subject = lines[0].replace("Subject:", "").strip() if lines and lines[0].startswith("Subject:") else ""
    date_line = next((l for l in lines if l.startswith("Date:")), "")

    # --- transactional templates ---

    if "Options strategy:" in joined:
        # Multi-leg combo fill (condor, spread, roll, etc.) - different template
        # from the single-leg "Account/Type/Option/Contracts/..." fill email.
        return _parse_multi_leg_fill(lines, date_line)

    if "Your order has been filled" in joined or "Your order has been partially filled" in joined:
        return _parse_order_fill(lines, date_line)

    if "Your order has been cancelled" in joined:
        return _parse_order_cancelled(lines, date_line)

    if "Your options have been exercised or assigned" in joined:
        return _parse_exercise_assignment(lines, date_line)

    if "You earned a dividend" in joined:
        return _parse_dividend(lines, date_line)

    if "You made a transfer" in joined:
        return _parse_transfer(lines, date_line)

    if "Your funds have been converted" in joined:
        return _parse_fx(lines, date_line)

    if "We got your direct deposit" in joined or ("Direct deposit received" in subject):
        return _parse_direct_deposit(lines, date_line)

    if "Wire transfer received" in subject or re.search(r"^Amount:.*\nFrom:.*\nTo:.*\nStatus:", joined, re.M):
        if "Wire transfer received" in joined:
            return _parse_wire_transfer(lines, date_line)

    # --- non-transactional / administrative, no holdings or cash impact ---
    ignored_markers = [
        "credit card payment", "credit card statement", "fee schedule",
        "account agreement", "crypto trading fees", "options expiring",
        "until your options expire",
    ]
    if any(m in joined.lower() or m in subject.lower() for m in ignored_markers):
        return {"transaction_type": "ignored", "subject": subject, "date": date_line}

    return {"transaction_type": "unrecognized", "subject": subject, "date": date_line}


_MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}


def _infer_expiry_iso(month_name, day, effective_dt):
    """Multi-leg fill legs give expiry as 'October 23' with no year. Infer it
    relative to the fill's own date: if the named month already passed this
    year (relative to the fill), the expiry must be next year."""
    month = _MONTH_MAP.get(month_name)
    if not month or effective_dt is None:
        return f"{month_name} {day}"
    year = effective_dt.year
    if month < effective_dt.month:
        year += 1
    return f"{year}-{month:02d}-{int(day):02d}"


def _parse_multi_leg_fill(lines, date_line):
    joined = "\n".join(lines)
    account = _field(lines, "Account")
    order_type = _field(lines, "Type")
    strategy_m = re.search(r"Options strategy:\s*([A-Za-z ]+?)(?:\n|Contracts:|$)", joined)
    strategy = strategy_m.group(1).strip() if strategy_m else None
    time_raw = _field(lines, "Time")

    effective_dt = None
    m = re.search(r"([A-Za-z]+ \d{1,2}, \d{4})", time_raw or "")
    if m:
        try:
            effective_dt = datetime.strptime(m.group(1), "%B %d, %Y")
        except Exception:
            effective_dt = None

    leg_pattern = re.compile(
        r"(Sell|Buy) to (open|close) (\d+) ([A-Z]+) ([A-Za-z]+) (\d{1,2}) \$?([\d.]+) (Call|Put)"
    )
    legs = []
    for lm in leg_pattern.finditer(joined):
        action, open_close, qty, underlying, month_name, day, strike, opt_type = lm.groups()
        legs.append({
            "action": action.upper(),
            "open_close": open_close.upper(),
            "quantity": int(qty),
            "underlying": underlying,
            "expiry": _infer_expiry_iso(month_name, day, effective_dt),
            "strike": float(strike),
            "option_type": opt_type.lower(),
        })

    # Net cash flow: "Total cost" = net debit paid (outflow), "Total premium" =
    # net credit received (inflow). Single-leg emails use "Total value" but
    # those never reach this parser (no "Options strategy:" field).
    cost_raw = _field(lines, "Total cost")
    premium_raw = _field(lines, "Total premium")
    if cost_raw:
        currency, amount = _money(cost_raw)
        net_cash = -amount if amount is not None else None
    elif premium_raw:
        currency, amount = _money(premium_raw)
        net_cash = amount
    else:
        currency, net_cash = None, None

    underlyings = sorted(set(l["underlying"] for l in legs))

    return {
        "transaction_type": "multi_leg_option_fill",
        "account": account,
        "order_type": order_type,
        "strategy": strategy,
        "legs": legs,
        "underlying": underlyings[0] if len(underlyings) == 1 else "/".join(underlyings),
        "net_cash": net_cash,
        "net_cash_currency": currency,
        "time": time_raw,
        "date": date_line,
    }


def _parse_order_fill(lines, date_line):
    account = _field(lines, "Account")
    order_type = _field(lines, "Type")  # e.g. "Market Sell to Open", "Fractional Buy"
    option_desc = _field(lines, "Option")
    symbol = _field(lines, "Symbol")
    contracts = _field(lines, "Contracts")
    shares = _field(lines, "Shares")
    expiry = _field(lines, "Expiry")
    avg_price_raw = _field(lines, "Average price")
    total_raw = _field(lines, "Total value") or _field(lines, "Total cost")
    time_raw = _field(lines, "Time")
    partial = any("partially filled" in l for l in lines)

    avg_currency, avg_price = _money(avg_price_raw)
    _, total_value = _money(total_raw)

    is_option = option_desc is not None
    action = "SELL" if order_type and "sell" in order_type.lower() else (
        "BUY" if order_type and "buy" in order_type.lower() else None
    )
    open_close = "OPEN" if order_type and "open" in order_type.lower() else (
        "CLOSE" if order_type and "close" in order_type.lower() else None
    )

    result = {
        "transaction_type": "option_fill" if is_option else "stock_fill",
        "partial": partial,
        "account": account,
        "action": action,
        "order_type": order_type,
        "avg_price": avg_price,
        "avg_price_currency": avg_currency,
        "total_value": total_value,
        "time": time_raw,
        "date": date_line,
    }

    if is_option:
        om = re.match(r"([A-Z]+)\s+([\d.]+)\s+(call|put)", option_desc, re.IGNORECASE)
        result.update({
            "underlying": om.group(1) if om else None,
            "strike": float(om.group(2)) if om else None,
            "option_type": om.group(3).lower() if om else None,
            "open_close": open_close,
            "contracts": int(contracts) if contracts and contracts.strip().isdigit() else contracts,
            "expiry": expiry,
            "raw_option_desc": option_desc,
        })
    else:
        # Shares field can be "77.6352" (full fill) or "474 of 474.0385" (partial)
        filled_shares = shares
        total_shares = shares
        if shares and " of " in shares:
            parts = shares.split(" of ")
            filled_shares, total_shares = parts[0].strip(), parts[1].strip()
        result.update({
            "symbol": symbol,
            "shares_filled": float(filled_shares) if filled_shares else None,
            "shares_ordered": float(total_shares) if total_shares else None,
        })

    return result


def _parse_order_cancelled(lines, date_line):
    account = _field(lines, "Account")
    order_type = _field(lines, "Type")
    option_desc = _field(lines, "Option")
    symbol = _field(lines, "Symbol")
    return {
        "transaction_type": "order_cancelled",
        "account": account,
        "order_type": order_type,
        "raw_option_desc": option_desc,
        "symbol": symbol,
        "date": date_line,
    }


def _parse_exercise_assignment(lines, date_line):
    joined = "\n".join(lines)
    account = _field(lines, "Account")
    date_m = re.search(r"exercised and assigned on ([A-Za-z]+ \d+, \d{4})", joined, re.IGNORECASE)
    opt_m = re.search(r"([A-Z]+)\s+\$?([\d.]+)\s+(Put|Call)", joined)
    contracts_m = re.search(r"(-?\d+)\s+contracts", joined)
    outcome_m = re.search(r"(Assigned|Exercised)", joined)
    action_m = re.search(r"(Bought|Sold)\s+([\d.]+)\s+shares", joined)
    total_cost_m = re.search(r"Total Cost:\s*\$?([\d,]+\.?\d*)\s*(USD|CAD)?", joined)

    return {
        "transaction_type": "option_exercise_assignment",
        "account": account,
        "event_date": date_m.group(1) if date_m else None,
        "underlying": opt_m.group(1) if opt_m else None,
        "strike": float(opt_m.group(2)) if opt_m else None,
        "option_type": opt_m.group(3).lower() if opt_m else None,
        "contracts": int(contracts_m.group(1)) if contracts_m else None,
        "outcome": outcome_m.group(1) if outcome_m else None,
        "share_action": action_m.group(1).upper() if action_m else None,
        "shares": float(action_m.group(2)) if action_m else None,
        "total_cost": float(total_cost_m.group(1).replace(",", "")) if total_cost_m else None,
        "total_cost_currency": (total_cost_m.group(2) if total_cost_m and total_cost_m.group(2) else None),
        "date": date_line,
    }


def _parse_dividend(lines, date_line):
    account = _field(lines, "Account")
    amount_raw = _field(lines, "Amount")
    symbol = _field(lines, "Symbol")
    _, amount = _money(amount_raw)
    return {
        "transaction_type": "dividend",
        "account": account,
        "symbol": symbol,
        "amount": amount,
        "date": date_line,
    }


def _parse_transfer(lines, date_line):
    amount_raw = _field(lines, "Amount")
    src = _field(lines, "From")
    dst = _field(lines, "To")
    currency, amount = _money(amount_raw)
    return {
        "transaction_type": "internal_transfer",
        "amount": amount,
        "currency": currency,
        "from_account": src,
        "to_account": dst,
        "date": date_line,
    }


def _parse_fx(lines, date_line):
    account = _field(lines, "Account")
    submitted_raw = _field(lines, "Amount submitted")
    received_raw = _field(lines, "Amount received")
    scur, samt = _money(submitted_raw)
    rcur, ramt = _money(received_raw)
    return {
        "transaction_type": "fx_conversion",
        "account": account,
        "amount_submitted": samt,
        "submitted_currency": scur,
        "amount_received": ramt,
        "received_currency": rcur,
        "date": date_line,
    }


def _parse_direct_deposit(lines, date_line):
    amount_raw = _field(lines, "Amount")
    src = _field(lines, "From")
    _, amount = _money(amount_raw)
    return {
        "transaction_type": "direct_deposit",
        "amount": amount,
        "from_source": src,
        "date": date_line,
    }


def _parse_wire_transfer(lines, date_line):
    amount_raw = _field(lines, "Amount")
    src = _field(lines, "From")
    dst = _field(lines, "To")
    status = _field(lines, "Status")
    currency, amount = _money(amount_raw)
    return {
        "transaction_type": "wire_transfer",
        "amount": amount,
        "currency": currency,
        "from_source": src,
        "to_account": dst,
        "status": status,
        "date": date_line,
    }
