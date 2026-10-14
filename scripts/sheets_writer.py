"""Thin Google Sheets wrapper for the "Tim's Desk Prices" sheet, authenticated
as a service account (key JSON in the GOOGLE_SERVICE_ACCOUNT_KEY env var /
GitHub Actions secret). No OAuth consent flow, no refresh-token expiry.

The sheet must be shared with the service account's client_email as Editor.
"""
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1rKLxaCrsAkvYPAff77JrOjBbfr3ix2CmClVhx7td03c"  # "Tim's Desk Prices"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_DEFAULT_TAB_NAMES = {"Untitled", "Sheet1"}
_service = None


def _svc():
    global _service
    if _service is None:
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        _service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _service


def col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def get_values(sheet_range: str) -> list[list[str]]:
    """Formatted values; empty rows come back as [] so row positions are kept."""
    res = _svc().spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=sheet_range
    ).execute()
    return res.get("values", [])


def update_range(sheet_range: str, rows: list[list], raw: bool = False):
    _svc().spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_range,
        valueInputOption="RAW" if raw else "USER_ENTERED",
        body={"values": rows},
    ).execute()


def clear_range(sheet_range: str):
    _svc().spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=sheet_range, body={}
    ).execute()


def ensure_sheet_exists(title: str, rows: int = 2000, cols: int = 30):
    """Make sure a tab named `title` exists, with at least rows x cols cells.
    Idempotent. A lone default-named tab ("Untitled"/"Sheet1") is renamed
    rather than left as an orphan next to a new empty one."""
    meta = _svc().spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = meta["sheets"]
    for s in sheets:
        p = s["properties"]
        if p["title"] == title:
            g = p["gridProperties"]
            if g["rowCount"] < rows or g["columnCount"] < cols:
                _svc().spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [{
                    "updateSheetProperties": {
                        "properties": {"sheetId": p["sheetId"], "gridProperties": {
                            "rowCount": max(rows, g["rowCount"]), "columnCount": max(cols, g["columnCount"])}},
                        "fields": "gridProperties.rowCount,gridProperties.columnCount",
                    }}]}).execute()
            return
    if len(sheets) == 1 and sheets[0]["properties"]["title"] in _DEFAULT_TAB_NAMES:
        req = {"updateSheetProperties": {
            "properties": {"sheetId": sheets[0]["properties"]["sheetId"], "title": title},
            "fields": "title"}}
    else:
        req = {"addSheet": {"properties": {
            "title": title, "gridProperties": {"rowCount": rows, "columnCount": cols}}}}
    _svc().spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [req]}).execute()


def write_table(title: str, rows: list[list], raw: bool = False):
    """Replace a machine-owned tab's contents with `rows` (header first)."""
    width = max(len(r) for r in rows)
    ensure_sheet_exists(title, rows=len(rows) + 50, cols=max(width, 10))
    clear_range(f"{title}!A1:ZZ")
    update_range(f"{title}!A1:{col_letter(width)}{len(rows)}", rows, raw=raw)
