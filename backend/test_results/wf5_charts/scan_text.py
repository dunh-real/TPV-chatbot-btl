"""Quét chữ trong mọi .pptx của một lượt chạy: tên cột CSDL gốc, chữ dạng tên trường, chữ mất dấu, chữ NFD.
    python scan_text.py ../runs/r3"""

import json
import re
import sys
import unicodedata
from pathlib import Path

from pptx import Presentation

from app.agents.nodes.presentation import _MAT_DAU
from app.documents.nhan_cot import la_ten_truong


def texts(path: Path) -> list[tuple[int, str]]:
    out = []
    for i, slide in enumerate(Presentation(str(path)).slides, start=1):
        stack = list(slide.shapes)
        while stack:
            shp = stack.pop()
            if shp.shape_type == 6:
                stack.extend(shp.shapes)
            if shp.has_text_frame and shp.text_frame.text.strip():
                out.append((i, shp.text_frame.text))
            if getattr(shp, "has_table", False) and shp.has_table:
                out += [(i, c.text) for row in shp.table.rows for c in row.cells if c.text.strip()]
    return out


root = Path(sys.argv[1])
total = {"files": 0, "texts": 0, "raw_column": 0, "field_like": 0, "lost": 0, "nfd": 0}
for q in sorted(p for p in root.iterdir() if p.is_dir()):
    data = json.loads((q / "data.json").read_text(encoding="utf-8"))
    # Tên cột gốc dài >= 5 ký tự (bỏ "thang", "nam" ngắn trùng chữ thường).
    raw = {c["name"] for d in data for c in d["columns"] if len(c["name"]) >= 5}
    pat = re.compile(r"\b(" + "|".join(map(re.escape, sorted(raw))) + r")\b", re.I) if raw else None
    for name in ("presenton.pptx", "local.pptx"):
        f = q / name
        if not f.exists():
            continue
        total["files"] += 1
        for slide, t in texts(f):
            total["texts"] += 1
            hits = []
            if pat and (m := pat.search(t)):
                hits.append(f"tên cột gốc {m.group(0)!r}")
                total["raw_column"] += 1
            if la_ten_truong(t):
                hits.append("dạng tên trường")
                total["field_like"] += 1
            if _MAT_DAU.search(t):
                hits.append("mất dấu")
                total["lost"] += 1
            if t != unicodedata.normalize("NFC", t):
                hits.append("NFD")
                total["nfd"] += 1
            if hits:
                print(q.name, name, f"slide {slide}:", "; ".join(hits), "|", t[:100].replace("\n", " "))
print(json.dumps(total, ensure_ascii=False))
