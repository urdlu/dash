"""Writes price data to the "Tim's Desk Prices" Google Sheet using a service
account - no OAuth consent flow, no refresh tokens, no 7-day expiry. The
service account's key is provided as a JSON string via the
GOOGLE_SERVICE_ACCOUNT_KEY environment variable (a GitHub Actions secret in
CI; a local file for manual testing - see README.md).

The target sheet must be shared with the service account's client_email as
an Editor before this will work.
"""
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1rKLxaCrsAkvYPAff77JrOjBbfr3ix2CmClVhx7td03c"  # "Tim's Desk Prices"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _get_service():
    key_json = os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"]
    info = json.loads(key_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def clear_range(sheet_range: str):
    service = _get_service()
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID, range=sheet_range, body={}
    ).execute()


def update_range(sheet_range: str, rows: list[list]):
    service = _get_service()
    body = {"values": rows}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_range,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


_DEFAULT_TAB_NAMES = {"Untitled", "Sheet1"}


def ensure_sheet_exists(title: str, rows: int = 500, cols: int = 30):
    """Make sure a tab named `title` exists. Idempotent, and self-healing for
    the common first-run case: a brand-new spreadsheet's only tab has
    Google's default name ("Untitled" for a CSV-uploaded sheet, "Sheet1" for
    a blank one) rather than the name this pipeline expects - rename it
    instead of leaving an orphan default tab plus a new empty one."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = meta["sheets"]
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in sheets}
    if title in existing:
        return

    renameable = [s for s in sheets if s["properties"]["title"] in _DEFAULT_TAB_NAMES]
    if len(sheets) == 1 and renameable:
        sheet_id = renameable[0]["properties"]["sheetId"]
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "title": title},
                    "fields": "title",
                }
            }]},
        ).execute()
        return

    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "addSheet": {"properties": {
                "title": title,
                "gridProperties": {"rowCount": rows, "columnCount": cols},
            }}
        }]},
    ).execute()
