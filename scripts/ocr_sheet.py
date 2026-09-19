"""Build a local HTML contact sheet for hand-transcribing the D4 OCR sample.

Shows every sampled image next to each model's transcription (as hints, clearly marked) and
the row's post_id; the human writes the true text into the `gold` column of labels.csv.

  uv run python scripts/ocr_sheet.py --dir data/eval/ocr   → data/eval/ocr/sheet.html
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path

STYLE = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:20px auto;padding:0 12px}
.row{display:grid;grid-template-columns:420px 1fr;gap:16px;border-top:1px solid #ccc;padding:14px 0}
img{max-width:420px;max-height:420px;border:1px solid #ddd}
pre{white-space:pre-wrap;background:#f5f5f5;padding:8px;margin:4px 0 10px;font-size:13px}
h3{margin:0 0 6px}small{color:#666}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=Path("data/eval/ocr"))
    args = ap.parse_args()
    rows = list(csv.DictReader((args.dir / "labels.csv").open(encoding="utf-8")))
    hints: dict[str, dict[str, str]] = {}
    for pred in sorted(args.dir.glob("pred_*.csv")):
        tag = pred.stem.removeprefix("pred_")
        for r in csv.DictReader(pred.open(encoding="utf-8")):
            hints.setdefault(r["post_id"], {})[tag] = r["ocr_text"]
    parts = [
        f"<title>OCR labels</title><style>{STYLE}</style>",
        "<h1>OCR sample — transcribe the text exactly as written</h1>",
        "<p>Write the true text into the <code>gold</code> column of <code>labels.csv</code> "
        "(line breaks as spaces are fine; keep case and punctuation). Model outputs below are "
        "hints only — they can be wrong.</p>",
    ]
    for i, r in enumerate(rows, 1):
        hint_html = "".join(
            f"<small>{html.escape(tag)}</small><pre>{html.escape(text) or '∅'}</pre>"
            for tag, text in sorted(hints.get(r["post_id"], {}).items())
        )
        parts.append(
            f'<div class="row"><div><img src="{html.escape(r["image"])}"></div>'
            f"<div><h3>#{i} · post_id {r['post_id']} · {r['topic']}</h3>{hint_html}</div></div>"
        )
    out = args.dir / "sheet.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
