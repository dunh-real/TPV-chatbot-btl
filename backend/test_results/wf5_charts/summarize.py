"""Tổng hợp results.json của eval_charts.py: bảng chỉ số và danh sách vấn đề.   python summarize.py runs/r1"""

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
results = json.loads((root / "results.json").read_text(encoding="utf-8"))

print("id  bộ  dòng        | Presenton: engine slide deck biểu_đồ ảnh/slide đối_chiếu giây | Lùi: slide ảnh "
      "cùng_hàm đối_chiếu | sai_số_biểu_đồ")
tong = {"p_ok": 0, "l_ok": 0, "charts": 0, "charts_ok": 0, "pic": 0, "same": 0, "bad": 0, "sec": []}
for r in results:
    if r.get("error"):
        print(r["id"], "LỖI", r["error"][:200])
        continue
    p, l = r["presenton"], r["local"]
    bad = sum(len(v) for v in r["chart_value_problems"].values())
    print(f"{r['id']} {r['datasets']}  {str(r['rows']):11} | {p['engine']:9} {p.get('slides')} "
          f"{str(p.get('deck_ok')):5} {p.get('charts_ok')}/{p.get('charts_total')} "
          f"{p.get('chart_slides_with_picture')}/{p.get('chart_slides')} {p.get('validation')!s:8} {p['seconds']:5} | "
          f"{l['slides']} {l['charts_in_file']} {l['charts_same_as_chart_for']} {l['validation']:8} | {bad}")
    tong["p_ok"] += p["engine"] == "presenton" and bool(p.get("deck_ok")) and p.get("validation") == "passed"
    tong["l_ok"] += l["validation"] == "passed"
    tong["charts"] += r["datasets"]
    tong["charts_ok"] += p.get("charts_ok") or 0
    tong["pic"] += p.get("chart_slides_with_picture") or 0
    tong["same"] += l["charts_same_as_chart_for"]
    tong["bad"] += bad
    tong["sec"].append(p["seconds"])
print(json.dumps({**tong, "sec": sorted(tong["sec"])}, ensure_ascii=False))
for r in results:
    for side in ("presenton", "local"):
        for i in r.get(side, {}).get("validation_issues", []):
            print(r["id"], side, i.get("type"), i.get("numbers") or i.get("characters"), i.get("quote", "")[:110])
        for i in r.get(side, {}).get("deck_issues", []):
            print(r["id"], side, "deck", i)
    for k, v in r.get("chart_value_problems", {}).items():
        for x in v:
            print(r["id"], k, x)
    if r.get("presenton", {}).get("assumptions"):
        print(r["id"], "giả định", r["presenton"]["assumptions"])
