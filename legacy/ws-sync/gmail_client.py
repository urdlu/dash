"""Minimal Gmail API client using only stdlib (no google-api-python-client
dependency, to sidestep the broken site-packages install on this machine).
Handles token refresh automatically.
"""
import json
import re
import urllib.parse
import urllib.request
import base64

import os

# Secrets were scrubbed before this reference copy was committed - never commit
# real client IDs/secrets. Set these via environment variables when porting.
CLIENT_ID = os.environ["WS_OAUTH_CLIENT_ID"]
CLIENT_SECRET = os.environ["WS_OAUTH_CLIENT_SECRET"]
TOKEN_PATH = os.environ.get("WS_TOKEN_PATH", "ws_combined_token.json")


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


def get_access_token():
    tok = _load_token()
    return tok["access_token"], tok


def _api_get(path, access_token, params=None):
    url = f"https://gmail.googleapis.com/gmail/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 401:
            raise PermissionError(f"401 Unauthorized: {body}")
        raise


def api_get_with_refresh(path, params=None):
    access_token, tok = get_access_token()
    try:
        return _api_get(path, access_token, params)
    except PermissionError:
        tok = _refresh_access_token(tok)
        return _api_get(path, tok["access_token"], params)


def list_messages(query, max_results=10):
    result = api_get_with_refresh(
        "users/me/messages", params={"q": query, "maxResults": max_results}
    )
    return result.get("messages", [])


def get_message(msg_id):
    return api_get_with_refresh(f"users/me/messages/{msg_id}", params={"format": "full"})


def get_attachment(msg_id, attachment_id):
    result = api_get_with_refresh(f"users/me/messages/{msg_id}/attachments/{attachment_id}")
    data_b64 = result["data"]
    return base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4))


def get_profile():
    return api_get_with_refresh("users/me/profile")


def _decode_part(data_b64):
    return base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4)).decode("utf-8", "replace")


def _looks_like_html(s):
    head = s.lstrip()[:200].lower()
    return head.startswith("<!doctype") or "<html" in head


def _html_to_text(h):
    t = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", h)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|tr|td|th|li|h[1-6])>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return "\n".join(line.strip() for line in t.split("\n") if line.strip())


def extract_body(msg):
    """Extract a clean plain-text body from a Gmail API message resource,
    same robustness as fetch_ws_attachments.py's .eml parser: handles a
    genuine text/plain part, a text/html part needing tag-stripping, and a
    text/plain part that's actually mislabeled raw HTML from the sender."""
    payload = msg.get("payload", {})
    plain = None
    html = None

    def walk(part):
        nonlocal plain, html
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if mime == "text/plain" and body.get("data") and plain is None:
            plain = _decode_part(body["data"])
        elif mime == "text/html" and body.get("data") and html is None:
            html = _decode_part(body["data"])
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)

    if plain and len(plain.strip()) > 20 and _looks_like_html(plain):
        plain = None
    if plain and len(plain.strip()) > 20:
        return plain
    if html:
        return _html_to_text(html)
    return ""


def header(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


if __name__ == "__main__":
    profile = get_profile()
    print("Authenticated as:", profile.get("emailAddress"))
    print("Total messages:", profile.get("messagesTotal"))

    print("\nSearching for Wealthsimple emails...")
    msgs = list_messages("from:wealthsimple OR subject:wealthsimple", max_results=10)
    print(f"Found {len(msgs)} matching message(s)")

    for m in msgs[:5]:
        full = get_message(m["id"])
        print("-" * 60)
        print("Subject:", header(full, "Subject"))
        print("From:", header(full, "From"))
        print("Date:", header(full, "Date"))
