"""So sánh MÔ TẢ đầu ra của rule-based và parser văn phạm trên PDF thật (không có ground truth).

Không chấm đúng/sai. Chỉ báo: mỗi phương pháp ra bao nhiêu tiêu đề, phân theo dạng mở đầu và theo cấp,
hai phương pháp trùng nhau bao nhiêu (ghép gần đúng, giữ thứ tự, cùng trang +-1), và in dàn ý ra Markdown.

    uv run python tests/layout_experiment/compare_methods.py --doc 12
Ra test_results/layout_grammar/compare/<doc>/{comparison.json, comparison.txt, outline_<phương pháp>.md}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("LAYOUT_WORK", BACKEND / "test_results" / "layout_grammar"))
sys.path[:0] = [str(Path(__file__).resolve().parent), str(BACKEND)]

from app.documents.outline import build_outline  # noqa: E402
from app.documents.parser import parse_document  # noqa: E402

SIM = 0.72


def norm(t: str) -> str:
    t = t.replace("“", "").replace("”", "").replace('"', "").replace("Ð", "Đ")
    return " ".join(t.split()).lower()[:90]


def strip_marks(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").replace("đ", "d")


def sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    n = min(len(a), len(b), 60)                     # so phần đầu: tiêu đề dài bị cắt / nối dòng khác nhau
    if n < 4:
        return 0.0
    return max(SequenceMatcher(None, a[:n], b[:n]).ratio(),
               SequenceMatcher(None, strip_marks(a[:n]), strip_marks(b[:n])).ratio())


def align(pred: list[dict], gold: list[dict]) -> list[tuple[int, int]]:
    n, m = len(pred), len(gold)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(dp[i + 1][j], dp[i][j + 1])
            if abs((pred[i].get("page") or 0) - gold[j]["page"]) <= 1 and sim(pred[i]["text"], gold[j]["text"]) >= SIM:
                best = max(best, dp[i + 1][j + 1] + 1)
            dp[i][j] = best
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if (abs((pred[i].get("page") or 0) - gold[j]["page"]) <= 1 and sim(pred[i]["text"], gold[j]["text"]) >= SIM
                and dp[i][j] == dp[i + 1][j + 1] + 1):
            pairs.append((i, j)); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


FORMS = [
    ("Chương", r"^chương\b"), ("Mục", r"^mục\b"), ("Điều (văn bản này)", r"^điều\s+\d"),
    ("Điều trong trích dẫn “…", r"^[“\"]\s*điều\s+\d"), ("lệnh sửa đổi …như sau:", r"như sau\s*:?\s*$"),
    ("khoản 1. 2.", r"^[“\"]?\d+[a-z]?\.\s"), ("điểm a) b)", r"^[“\"]?[a-zđ]\)\s"),
    ("Phụ lục", r"^phụ lục"), ("chữ HOA", None),
]


def form_of(text: str) -> str:
    t = " ".join(text.split()).lower()
    for name, pat in FORMS:
        if pat and re.search(pat, t):
            return name
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.9:
        return "chữ HOA"
    return "khác"


def describe(heads: list[dict]) -> dict:
    return {"headings": len(heads), "by_level": dict(sorted(Counter(h["level"] for h in heads).items())),
            "by_form": dict(Counter(form_of(h["text"]) for h in heads).most_common()),
            "pages_with_headings": len({h.get("page") for h in heads})}


def outline_md(name: str, heads: list[dict]) -> str:
    lines = [f"# Dàn ý - {name} ({len(heads)} tiêu đề)", ""]
    for h in heads:
        lines.append(f"{'  ' * (h['level'] - 1)}- [L{h['level']}, tr.{h.get('page')}] {h['text'][:140]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default="12")
    args = ap.parse_args()
    base = WORK
    pdf = base / "data" / "pdf" / f"{args.doc}.pdf"
    structure = parse_document(pdf)
    page_of = {b.id: b.page for b in structure.blocks}
    outline = build_outline(structure)
    methods = {"rule_based": [{"level": h.level, "text": " ".join(h.text.split()), "page": page_of.get(h.block_id)}
                              for h in outline.headings]}

    out = base / "compare" / args.doc
    out.mkdir(parents=True, exist_ok=True)
    report = {"document": args.doc, "ground_truth": "unavailable", "evaluation_type": "so sánh mô tả giữa các phương pháp",
              "rule_based_title": outline.title.text if outline.title else None,
              "methods": {m: describe(h) for m, h in methods.items()}, "overlap": {}}
    names = list(methods)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs = align(methods[a], methods[b])
            both = len(pairs)
            same_level = sum(methods[a][x]["level"] == methods[b][y]["level"] for x, y in pairs)
            only_a = [methods[a][x]["text"][:90] for x in sorted(set(range(len(methods[a]))) - {x for x, _ in pairs})]
            only_b = [methods[b][y]["text"][:90] for y in sorted(set(range(len(methods[b]))) - {y for _, y in pairs})]
            report["overlap"][f"{a} vs {b}"] = {
                "both": both, f"only_{a}": len(only_a), f"only_{b}": len(only_b),
                "jaccard": round(both / max(1, len(methods[a]) + len(methods[b]) - both), 4),
                "same_level_when_both": round(same_level / both, 4) if both else None,
                f"examples_only_{a}": only_a[:10], f"examples_only_{b}": only_b[:10],
                f"forms_only_{a}": dict(Counter(form_of(t) for t in only_a).most_common()),
                f"forms_only_{b}": dict(Counter(form_of(t) for t in only_b).most_common())}
    for m, h in methods.items():
        (out / f"outline_{m}.md").write_text(outline_md(m, h), encoding="utf-8")
    (out / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    L = [f"Tài liệu {args.doc} | Ground truth: unavailable | Evaluation type: so sánh mô tả", ""]
    for m, d in report["methods"].items():
        L += [f"{m}: {d['headings']} tiêu đề, {d['pages_with_headings']} trang | cấp {d['by_level']}",
              f"    dạng: {d['by_form']}"]
    L.append("")
    for k, v in report["overlap"].items():
        a, b = k.split(" vs ")
        L += [f"{k}: chung {v['both']} | chỉ {a} {v[f'only_{a}']} | chỉ {b} {v[f'only_{b}']} | Jaccard {v['jaccard']} | cùng cấp khi chung {v['same_level_when_both']}",
              f"    chỉ {a} theo dạng: {v[f'forms_only_{a}']}", f"    chỉ {b} theo dạng: {v[f'forms_only_{b}']}"]
    (out / "comparison.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
