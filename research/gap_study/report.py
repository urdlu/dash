"""Build report.html from report_template.html + data/results.json."""
import json
from pathlib import Path

HERE = Path(__file__).parent


def rounded(o):
    if isinstance(o, float):
        return round(o, 4)
    if isinstance(o, dict):
        return {k: rounded(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rounded(v) for v in o]
    return o


res = rounded(json.loads((HERE / "data" / "results.json").read_text(encoding="utf-8")))
html = (HERE / "report_template.html").read_text(encoding="utf-8")
(HERE / "report.html").write_text(html.replace("__DATA__", json.dumps(res, ensure_ascii=False)), encoding="utf-8")
print("wrote", HERE / "report.html")
