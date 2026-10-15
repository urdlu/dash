# WS sync on Google Apps Script

Replaces the laptop desktop task. Runs hourly inside **wstradesws@gmail.com**, free, no OAuth tokens to expire, no Claude tokens.

The parser is a line-for-line port of `legacy/ws-sync/ws_parser.py` + `_to_row`, parity-tested against the Python on 18 email variants (fills, partials, options, multi-leg, exercise, dividends, transfers, FX, deposits, wires, ignored, odd dates, unicode junk) plus body extraction from plain/HTML/mislabeled parts — identical output on all.

## One-time setup (~5 min)

1. In a browser **signed in as wstradesws@gmail.com**, go to <https://script.google.com> → **New project**. Name it `WS Sync`.
2. Paste `Code.gs` from this folder over the default code.
3. ⚙️ **Project Settings** → tick **Show "appsscript.json" manifest file** → back in the editor, open `appsscript.json` and replace it with the one in this folder (this turns on the Gmail advanced service and sets the Vancouver timezone).
4. Optional phone alerts: ⚙️ Project Settings → **Script Properties** → add `NTFY_TOPIC` = your ntfy topic (same one as the morning push).
5. In the function dropdown pick **`dryRun`** → **Run** → approve the permission prompts ("unverified app" → Advanced → Go to WS Sync). Check the log: it lists what it *would* add.
6. Pick **`setup`** → **Run**. That does the first real sync and installs the hourly trigger.
7. **Turn off the old WS sync scheduled task** in the Claude desktop app, or both will write to the sheet.

## Notes

- **State** lives in a hidden `_ws_sync_state` tab of WS Transactions Log (processed message IDs + last-checked date).
- **First run** has no saved IDs (the old `sync_state.json` stayed on the laptop), so it skips any email whose row (date + type + detail) is already in the log. That safely backfills whatever the dead laptop job missed.
- **Failures** send an ntfy push (if `NTFY_TOPIC` is set), and Google also emails trigger failures to wstradesws.
- New rows are appended oldest-first (the Python appended newest-first).
- If you change parsing rules, change them here and in the Python reference together, and rerun the parity test in `test/`.
