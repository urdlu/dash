"""Parity test: Apps Script parser (Code.gs) vs the original Python
(legacy/ws-sync). Run from repo root:  python apps-script/ws-sync/test/parity_test.py
Needs python3 + node."""
import json, os, shutil, subprocess, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "legacy", "ws-sync"))
sys.path.insert(0, HERE)
for n in ("gmail_client", "sheets_client"):
    sys.modules[n] = types.ModuleType(n)
import ws_sync  # noqa: E402
from ws_parser import parse  # noqa: E402
from ws_cases import CASES  # noqa: E402

py = {k: ws_sync._to_row(parse(v)) for k, v in CASES.items()}

tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(HERE, "..", "Code.gs"), os.path.join(tmp, "code.js"))
with open(os.path.join(tmp, "cases.json"), "w") as f:
    json.dump(CASES, f)
js_src = "const c=require('./code.js'),C=require('./cases.json'),o={};" \
         "for(const k in C)o[k]=c.toRow(c.parse(C[k]));console.log(JSON.stringify(o))"
js = json.loads(subprocess.check_output(["node", "-e", js_src], cwd=tmp))

bad = [k for k in py if py[k] != js[k]]
for k in sorted(py):
    print(("DIFF" if k in bad else "OK  "), k, "" if k not in bad else f"\n  py={py[k]}\n  js={js[k]}")
print(f"{len(py) - len(bad)}/{len(py)} identical")
sys.exit(1 if bad else 0)
