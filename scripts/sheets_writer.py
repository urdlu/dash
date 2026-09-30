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


def update_range(sheet_range: str, rows: list[list]):
    service = _get_service()
    body = {"values": rows}
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=sheet_range,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


def ensure_sheet_exists(title: str, rows: int = 500, cols: int = 30):
    """Create a tab if it doesn't already exist. Idempotent."""
    service = _get_service()
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    existing = {s["properties"]["title"] for s in meta["sheets"]}
    if title in existing:
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
