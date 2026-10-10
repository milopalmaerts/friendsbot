"""Build the journal website: website/index.html (self-contained, journal data inlined).

Run `python -m friendsbot paper` first to update reports/paper/journal.json.
Also writes dist/artifact.html (the same page without the document wrapper).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent


def build() -> str:
    journal = json.loads((ROOT / "reports" / "paper" / "journal.json").read_text())
    sandbox_path = ROOT / "reports" / "live" / "sandbox.json"
    if sandbox_path.exists():
        sb = json.loads(sandbox_path.read_text())
        journal["sandbox"] = {k: v for k, v in sb.items() if k != "trades"}
        journal["trades"] = journal["trades"] + sb["trades"]
    body = (HERE / "journal.html").read_text().replace("/*__DATA__*/null", json.dumps(journal, separators=(",", ":")))
    page = ("<!doctype html>\n<html lang=\"nl\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"</head>\n<body>\n{body}\n</body>\n</html>\n")
    target = HERE / "index.html"
    target.write_text(page)
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "artifact.html").write_text(body)
    return str(target)


if __name__ == "__main__":
    print(build())
