"""Minimal Google Sheets API client using only stdlib (mirrors gmail_client.py).
Authenticated as wstradesws@gmail.com, which has Editor access to the
WS Transactions Log sheet (owned by timluu@gmail.com, shared explicitly).
"""
import json
import urllib.parse
import urllib.request

import os

# Secrets were scrubbed before this reference copy was committed - never commit
# real client IDs/secrets. Set these via environment variables when porting.
CLIENT_ID = os.environ["WS_OAUTH_CLIENT_ID"]
CLIENT_SECRET = os.environ["WS_OAUTH_CLIENT_SECRET"]
TOKEN_PATH = os.environ.get("WS_TOKEN_PATH", "ws_combined_token.json")

SPREADSHEET_ID = "15JYrY5pq1D9Xe6wbL-f3G5GOn_0ggNeaqR1uCBic8Kw"


def _load_token():
    with open(TOKEN_PATH) as f:
        return json.load(f)


def _save_token(tok):
    with open(TOKEN_PATH, "w") as f:
        json.dump(tok, f, indent=2)


def _refresh_access_token(tok):
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        new_tok = json.loads(resp.read().decode())
    tok["access_token"] = new_tok["access_token"]
    tok["expires_in"] = new_tok.get("expires_in")
    _save_token(tok)
    return tok


def _request(method, path, access_token, params=None, body=None):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        if e.code == 401:
            raise PermissionError(f"401: {b}")
        raise RuntimeError(f"{e.code}: {b}")


def _with_refresh(method, path, params=None, body=None):
    tok = _load_token()
    try:
        return _request(method, path, tok["access_token"], params, body)
    except PermissionError:
        tok = _refresh_access_token(tok)
        return _request(method, path, tok["access_token"], params, body)


def batch_update(requests):
    return _with_refresh(
        "POST", f"{SPREADSHEET_ID}:batchUpdate", body={"requests": requests}
    )


def get_metadata():
    return _with_refresh("GET", SPREADSHEET_ID)


def get_values(sheet_range):
    return _with_refresh("GET", f"{SPREADSHEET_ID}/values/{urllib.parse.quote(sheet_range)}")


def append_rows(sheet_name, rows):
    """Append rows (list of lists) after the last row of data in sheet_name."""
    body = {"values": rows}
    return _with_refresh(
        "POST",
        f"{SPREADSHEET_ID}/values/{urllib.parse.quote(sheet_name)}:append",
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        body=body,
    )


def update_values(sheet_range, rows):
    """Overwrite an exact range with the given rows (list of lists)."""
    body = {"values": rows}
    return _with_refresh(
        "PUT",
        f"{SPREADSHEET_ID}/values/{urllib.parse.quote(sheet_range)}",
        params={"valueInputOption": "USER_ENTERED"},
        body=body,
    )


def clear_values(sheet_range):
    return _with_refresh(
        "POST", f"{SPREADSHEET_ID}/values/{urllib.parse.quote(sheet_range)}:clear"
    )


if __name__ == "__main__":
    meta = get_metadata()
    print("Spreadsheet title:", meta.get("properties", {}).get("title"))
    print("Sheets:")
    for s in meta.get("sheets", []):
        p = s["properties"]
        print(f"  - {p['title']!r} (sheetId={p['sheetId']}, rows={p['gridProperties']['rowCount']})")
