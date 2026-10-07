"""Kiểm đầu ra rule-based / parser văn phạm theo nguyentac.md (bố cục Phần > Chương > Mục > Điều > Khoản > Điểm,
quy tắc đánh số, quy tắc văn bản sửa đổi). Không phải ground truth: đây là kiểm TÍNH HỢP LỆ cấu trúc.

    uv run python tests/layout_experiment/check_rules.py --doc 12
Ra test_results/layout_grammar/compare/<doc>/rules_check.{json,txt}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("LAYOUT_WORK", BACKEND / "test_results" / "layout_grammar"))
sys.path[:0] = [str(Path(__file__).resolve().parent), str(BACKEND)]

import pymupdf  # noqa: E402

from compare_methods import form_of  # noqa: E402

VN_LETTERS = "abcdđeghiklmnopqrstuvxy"           # chữ cái tiếng Việt dùng cho Điểm (không f, j, w, z)
RANK = {"Phần": 0, "Chương": 1, "Mục": 2, "Điều": 3, "Khoản": 4, "Điểm": 5}
OWN_DIEU = re.compile(r"^Điều\s+(\d+)([a-z]?)\.\s+\S")           # Điều của chính văn bản (không có ngoặc kép mở)
QUOTED_DIEU = re.compile(r"^[“\"]\s*Điều\s+(\d+)([a-z]?)\.\s+\S")


def unit_of(text: str) -> str | None:
    """Đơn vị cấu trúc theo nguyentac.md, nhận từ dạng mở đầu của dòng."""
    t = " ".join(text.split())
    q = t.lstrip("“\"").strip()
    if re.match(r"^PHẦN\s+[IVXLC]+\b", q, re.I): return "Phần"
    if re.match(r"^Chương\s+[IVXLC]+\b", q, re.I): return "Chương"
    if re.match(r"^Mục\s+\d+\b", q, re.I): return "Mục"
    if re.match(r"^Điều\s+\d+[a-z]?\.", q): return "Điều"
    if re.match(r"^\d+[a-z]?\.\s", q): return "Khoản"
    if re.match(rf"^[{VN_LETTERS}]\)\s", q): return "Điểm"
    return None


def reference_own_articles(pdf: Path) -> list[dict]:
    """Danh sách Điều của CHÍNH văn bản đọc từ lớp chữ OCR theo quy tắc mục 2 ('Điều: số Ả Rập, sau số có dấu .,
    có tên điều') và mục 5 (điều trích dẫn sửa đổi nằm trong ngoặc kép)."""
    out = []
    for pi, page in enumerate(pymupdf.open(pdf)):
        for line in page.get_text().splitlines():
            m = OWN_DIEU.match(line.strip())
            if m:
                out.append({"page": pi + 1, "num": int(m.group(1)), "suffix": m.group(2), "text": line.strip()})
    return out


def check(heads: list[dict], own_ref: list[dict]) -> dict:
    units = [unit_of(h["text"]) for h in heads]
    quoted = [h["text"].lstrip().startswith(("“", '"')) for h in heads]
    lv = [h["level"] for h in heads]
    # (1) Thứ bậc: đơn vị lớn hơn phải có cấp nhỏ hơn (nông hơn) khi xuất hiện liền trước nó trong cùng nhánh.
    order_viol, examples = 0, []
    stack: list[tuple[int, str, str]] = []
    amend_nest = 0
    for h, u, l in zip(heads, units, lv):
        while stack and stack[-1][0] >= l:
            stack.pop()
        if u and stack and stack[-1][1] and RANK[stack[-1][1]] >= RANK[u]:
            # Mục 5 nguyentac.md: khoản/điểm "... như sau:" chứa nội dung trích dẫn (điều/khoản/điểm được
            # sửa đổi) là lồng HỢP LỆ, không phải vi phạm thứ bậc.
            if " ".join(stack[-1][2].split()).rstrip().endswith(("như sau:", "như sau")):
                amend_nest += 1
            else:
                order_viol += 1
                if len(examples) < 5:
                    examples.append(f"{stack[-1][1]} (L{stack[-1][0]}) chứa {u} (L{l}): {h['text'][:60]}")
        stack.append((l, u, h["text"]))
    # (2) Cùng đơn vị (ngoài trích dẫn) phải cùng cấp.
    level_by_unit: dict[str, Counter] = {}
    for u, l, q in zip(units, lv, quoted):
        if u and not q:
            level_by_unit.setdefault(u, Counter())[l] += 1
    inconsistent = {u: dict(c) for u, c in level_by_unit.items() if len(c) > 1}
    # (3) Điều của chính văn bản: phủ được bao nhiêu điều tham chiếu, có đúng cấp nông nhất không.
    own_found = [h for h, u, q in zip(heads, units, quoted) if u == "Điều" and not q]
    # Điều thật của văn bản sửa đổi là dãy liền 1..N; Điều 33-38, 4b đọc được từ OCR là điều TRÍCH DẪN
    # bị mất dấu ngoặc kép mở. Lấy dãy liền dài nhất bắt đầu từ 1, không hậu tố.
    seq, n = [], 1
    for r in own_ref:
        if r["num"] == n and not r["suffix"]:
            seq.append(r); n += 1
    ref_keys = {(r["num"], "") for r in seq}
    got_keys = set()
    for h in own_found:
        m = OWN_DIEU.match(" ".join(h["text"].split()))
        if m:
            got_keys.add((int(m.group(1)), m.group(2)))
    top = min(lv) if lv else None
    # (4) Điểm đúng bảng chữ cái tiếng Việt; (5) điều trích dẫn sửa đổi nằm SÂU hơn Điều của văn bản.
    bad_letters = [h["text"][:40] for h, u in zip(heads, units)
                   if u == "Điểm" and h["text"].lstrip("“\" ")[0].lower() in "fjwz"]
    own_levels = {h["level"] for h in own_found}
    quoted_dieu = [h for h, u, q in zip(heads, units, quoted) if u == "Điều" and q]
    quoted_not_deeper = sum(1 for h in quoted_dieu if own_levels and h["level"] <= min(own_levels))
    return {
        "headings": len(heads),
        "units": dict(Counter(u or "không phải đơn vị (nhiễu)" for u in units)),
        "quoted_units": dict(Counter(u for u, q in zip(units, quoted) if q and u)),
        "own_articles_found": len(got_keys & ref_keys), "own_articles_reference": len(ref_keys),
        "own_articles_missed": sorted(f"Điều {n}{s}" for n, s in ref_keys - got_keys),
        "own_articles_at_top_level": sum(1 for h in own_found if h["level"] == top),
        "order_violations": order_viol, "order_violation_examples": examples, "valid_amendment_nesting": amend_nest,
        "same_unit_different_levels": inconsistent,
        "quoted_articles": len(quoted_dieu), "quoted_articles_not_deeper_than_own": quoted_not_deeper,
        "non_vietnamese_point_letters": bad_letters,
        "noise_examples": [h["text"][:70] for h, u in zip(heads, units) if not u][:6],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default="12")
    args = ap.parse_args()
    base = WORK
    out = base / "compare" / args.doc
    own_ref = reference_own_articles(base / "data" / "pdf" / f"{args.doc}.pdf")
    nums = [r["num"] for r in own_ref]
    methods = {}
    for name, md in (("rule_based", "outline_rule_based.md"), ("grammar", "outline_grammar.md")):
        if not (out / md).exists():
            continue
        heads = []
        for line in (out / md).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*- \[L(\d+), tr\.(\w+)\] (.*)$", line)
            if m:
                heads.append({"level": int(m.group(1)), "page": m.group(2), "text": m.group(3)})
        methods[name] = check(heads, own_ref)
    report = {"reference_own_articles": [f"Điều {r['num']}{r['suffix']} (tr.{r['page']})" for r in own_ref],
              "reference_sequence_ok": nums == sorted(nums), "methods": methods}
    (out / "rules_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"Điều của chính văn bản (đọc từ OCR theo quy tắc): {len(own_ref)} -> {report['reference_own_articles']}", ""]
    for m, r in methods.items():
        L += [f"[{m}] {r['headings']} tiêu đề | đơn vị: {r['units']} | trong trích dẫn: {r['quoted_units']}",
              f"   Điều của văn bản: {r['own_articles_found']}/{r['own_articles_reference']} (ở cấp nông nhất: {r['own_articles_at_top_level']}) | thiếu: {r['own_articles_missed']}",
              f"   vi phạm thứ bậc: {r['order_violations']} {r['order_violation_examples'][:3]} | lồng sửa đổi hợp lệ: {r['valid_amendment_nesting']}",
              f"   cùng đơn vị khác cấp: {r['same_unit_different_levels']}",
              f"   Điều trích dẫn: {r['quoted_articles']} (không sâu hơn Điều của văn bản: {r['quoted_articles_not_deeper_than_own']})",
              f"   Điểm sai bảng chữ cái: {r['non_vietnamese_point_letters']} | nhiễu: {r['noise_examples'][:4]}", ""]
    (out / "rules_check.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
