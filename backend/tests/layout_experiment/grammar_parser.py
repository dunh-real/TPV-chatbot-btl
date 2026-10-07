"""Bộ phân tích cấu trúc văn bản pháp quy theo văn phạm + giải mã có ràng buộc (không LLM).

Căn cứ: tests/layout_experiment/nguyentac.md (Phần > Chương > Mục > Điều > Khoản > Điểm; đánh số; văn bản sửa đổi).
Ý tưởng như các parser văn bản luật dùng thật (Laws.Africa slaw/bluebell: văn phạm thay vì bố cục trang;
Akoma Ntoso: "cấu trúc trích dẫn" trong văn bản sửa đổi).

  1. Bỏ dòng rác của trình duyệt (ngày giờ in, about:blank, số trang).
  2. Mỗi dòng có tập nhãn khả dĩ từ dạng mở đầu: Phần/Chương/Mục/Điều n/Khoản k/Điểm x/thân bài.
  3. Beam search trên CẢ văn bản, luật chuyển theo nguyentac.md:
       Điều: n -> n+1 (hoặc n kèm hậu tố a, b);  Khoản: k -> k+1 trong một Điều;
       Điểm: a b c d đ e g ... (bảng chữ cái tiếng Việt) trong một Khoản;
       Khoản/Điểm của văn bản kết thúc "như sau:" MỞ phạm vi trích dẫn; đơn vị kế tiếp của văn bản ĐÓNG nó;
       trong Điều sửa đổi, Khoản/Điểm của chính văn bản phải có động từ sửa đổi - không có thì dòng đó
       nên hiểu là nội dung trích dẫn (vd. "2. Phạt tiền ..." nằm trong điều được chèn).
  4. Dựng cây: Điều L1 > Khoản L2 > Điểm L3 > đơn vị trích dẫn lồng dưới lệnh sửa đổi.
Dòng mà hai giả thuyết tốt nhất gần ngang điểm được ghi "ambiguous" - chỗ cho LLM dự phòng (chưa gọi).

    uv run python tests/layout_experiment/grammar_parser.py --doc 12
Ra test_results/layout_grammar/grammar/<doc>/{lines.json, outline.json, outline_grammar.md, stats.json}
và chép outline_grammar.md sang test_results/layout_grammar/compare/<doc>/ để check_rules.py chấm cùng bộ tiêu chí.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("LAYOUT_WORK", BACKEND / "test_results" / "layout_grammar"))
VN_LETTERS = "abcdđeghiklmnopqrstuvxy"
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
         "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20}

JUNK = [re.compile(p) for p in (r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*(AM|PM)?$", r"^about:blank$",
                                r"^\d{1,3}/\d{1,3}$", r"^about:blank\s+\d{1,3}/\d{1,3}$")]
QUOTE_OPEN = ("“", '"', "‘")
AMEND_VERB = re.compile(r"\b(sửa đổi|bổ sung|bãi bỏ|thay thế|thay cụm từ|bỏ cụm từ|đính chính|thay đổi)\b", re.I)
AS_FOLLOWS = re.compile(r"như sau\s*:?\s*[“\"]?\s*$|như sau\s*:\s*[“\"]", re.I)

RE_PHAN = re.compile(r"^PHẦN\s+(THỨ\s+\S+|[IVX]+)\b", re.I)
RE_CHUONG = re.compile(r"^CHƯƠNG\s+([IVX]+)\b", re.I)
RE_MUC = re.compile(r"^MỤC\s+(\d+)\b", re.I)
RE_DIEU = re.compile(r"^Điều\s+(\d+)([a-z]?)\.\s*\S")
RE_KHOAN = re.compile(r"^(\d{1,2})([a-z]?)\.\s+\S")
RE_DIEM = re.compile(rf"^([{VN_LETTERS}])(\d?)\)\s+\S")


def norm(t: str) -> str:
    return " ".join(t.replace("\xa0", " ").split())


def markers(text: str) -> dict:
    """Dạng mở đầu của dòng (bỏ dấu ngoặc kép mở để nhận cả đơn vị trong trích dẫn)."""
    quoted = text.startswith(QUOTE_OPEN)
    t = text.lstrip("“\"‘ ").strip()
    m = {"quoted": quoted, "as_follows": bool(AS_FOLLOWS.search(text)), "amend_verb": bool(AMEND_VERB.search(t[:120]))}
    if (x := RE_PHAN.match(t)):
        g = x.group(1).upper()
        m["phan"] = ROMAN.get(g) or ORDINAL_WORDS.get(g.split()[-1], 0)
    if (x := RE_CHUONG.match(t)): m["chuong"] = ROMAN.get(x.group(1).upper(), 0)
    if (x := RE_MUC.match(t)): m["muc"] = int(x.group(1))
    if (x := RE_DIEU.match(t)): m["dieu"] = (int(x.group(1)), x.group(2))
    if (x := RE_KHOAN.match(t)): m["khoan"] = (int(x.group(1)), x.group(2))
    if (x := RE_DIEM.match(t)): m["diem"] = (VN_LETTERS.index(x.group(1)) + 1, x.group(2))
    return m


@dataclass
class Hyp:
    score: float
    phan: int = 0
    chuong: int = 0
    muc: int = 0
    dieu: int = 0
    dieu_amending: bool = False
    khoan: int = 0
    diem: int = 0
    scope: bool = False                 # đang ở trong phạm vi trích dẫn mở bởi "như sau:"
    seen_dieu: bool = False
    labels: list = field(default_factory=list)

    def key(self):
        return (self.phan, self.chuong, self.muc, self.dieu, self.khoan, self.diem, self.scope,
                self.dieu_amending, self.seen_dieu)


def options(h: Hyp, m: dict, text: str) -> list[tuple[str, float, dict]]:
    """Các nhãn khả dĩ cho dòng hiện tại dưới giả thuyết h: (nhãn, chi phí, thay đổi trạng thái)."""
    out = []
    structural = any(k in m for k in ("dieu", "khoan", "diem", "phan", "chuong", "muc"))
    own_ok = not m["quoted"]            # luật cứng: dòng mở bằng ngoặc kép là nội dung trích dẫn, không phải của văn bản

    def step(cur: int, n: int, suf: str) -> float | None:
        """Chi phí theo bước nhảy của dãy số: liền mạch 0; hậu tố cùng số (4a sau 4) 0;
        nhảy 2-4 (OCR làm rơi đơn vị) phạt 1.5/bước thiếu; ngoài ra không hợp lệ."""
        if suf:
            return 0.0 if n == cur else None
        gap = n - cur
        if gap == 1:
            return 0.0
        if 2 <= gap <= 4:
            return 1.5 * (gap - 1)
        return None

    # Phần > Chương > Mục (nguyentac.md mục 1, 2): số La Mã cho Phần/Chương, số Ả Rập cho Mục; Mục đánh lại
    # trong mỗi Chương; số Điều chạy liên tục qua các Chương nên không đặt lại. Sau một đơn vị lớn, Khoản/Điểm
    # chỉ hợp lệ khi đã có Điều mới.
    if own_ok and m.get("phan"):
        c = step(h.phan, m["phan"], "")
        if c is not None:
            out.append(("OWN_PHAN", c, {"phan": m["phan"], "chuong": h.chuong, "muc": 0, "khoan": 0, "diem": 0,
                                        "scope": False, "seen_dieu": False}))
    if own_ok and m.get("chuong"):
        c = step(h.chuong, m["chuong"], "")
        if c is not None:
            out.append(("OWN_CHUONG", c, {"chuong": m["chuong"], "muc": 0, "khoan": 0, "diem": 0,
                                          "scope": False, "seen_dieu": False}))
    if own_ok and "muc" in m:
        c = step(h.muc, m["muc"], "")
        if c is not None:
            out.append(("OWN_MUC", c, {"muc": m["muc"], "khoan": 0, "diem": 0, "scope": False, "seen_dieu": False}))
    if own_ok and "dieu" in m:
        n, suf = m["dieu"]
        c = step(h.dieu, n, suf)
        if c is not None and suf and h.scope:
            c += 3.0                        # "Điều 4b." trong phạm vi trích dẫn: gần như chắc là điều được chèn, mất ngoặc kép do OCR
        if c is not None:
            out.append(("OWN_DIEU", c, {"dieu": n, "khoan": 0, "diem": 0, "scope": False, "seen_dieu": True,
                                        "dieu_amending": bool(AMEND_VERB.search(text))}))
    if own_ok and "khoan" in m and h.seen_dieu:
        k, suf = m["khoan"]
        c = step(h.khoan, k, suf)
        if c is not None:
            c += 3.0 if h.dieu_amending and not m["amend_verb"] else 0.0
            out.append(("OWN_KHOAN", c, {"khoan": k, "diem": 0, "scope": m["as_follows"]}))
    if own_ok and "diem" in m and h.seen_dieu and h.khoan > 0:
        d, suf = m["diem"]
        c = step(h.diem, d, suf)
        if c is not None:
            c += 3.0 if h.dieu_amending and not m["amend_verb"] else 0.0
            out.append(("OWN_DIEM", c, {"diem": d, "scope": m["as_follows"]}))
    if h.scope:
        # Trong phạm vi trích dẫn: mọi dòng đều có thể là nội dung được sửa đổi. Dòng có đánh số thì
        # đắt hơn chút để giải thích "của văn bản" thắng khi dãy số khớp VÀ có động từ sửa đổi.
        out.append(("QUOTED", 0.5 if structural else 0.0, {}))
    # Thân bài (gồm cả dòng nối của đoạn): miễn phí nếu không có dấu đánh số, phạt nếu có.
    body_cost = 2.5 if structural else 0.0
    if h.scope and structural:
        body_cost = 6.0                                   # trong trích dẫn thì đã có nhãn QUOTED rẻ hơn
    out.append(("BODY", body_cost, {}))
    return out


def decode(lines: list[dict], beam: int) -> tuple[list[str], list[dict]]:
    hyps = [Hyp(0.0)]
    ambiguous = []
    for i, ln in enumerate(lines):
        m = ln["m"]
        cand: dict[tuple, Hyp] = {}
        for h in hyps:
            for label, cost, upd in options(h, m, ln["text"]):
                nh = Hyp(h.score + cost, h.phan, h.chuong, h.muc, h.dieu, h.dieu_amending, h.khoan, h.diem,
                         h.scope, h.seen_dieu, h.labels + [label])
                for k, v in upd.items():
                    setattr(nh, k, v)
                kk = nh.key()
                if kk not in cand or cand[kk].score > nh.score:
                    cand[kk] = nh
        hyps = sorted(cand.values(), key=lambda x: x.score)[:beam]
        if len(hyps) > 1 and hyps[0].labels[-1] != hyps[1].labels[-1] and hyps[1].score - hyps[0].score < 1.0:
            ambiguous.append({"index": i, "page": ln["page"], "text": ln["text"][:100],
                              "best": hyps[0].labels[-1], "second": hyps[1].labels[-1],
                              "gap": round(hyps[1].score - hyps[0].score, 2)})
    return hyps[0].labels, ambiguous


def quoted_unit(m: dict) -> str | None:
    for k, name in (("phan", "Phần"), ("chuong", "Chương"), ("muc", "Mục"), ("dieu", "Điều"), ("khoan", "Khoản"), ("diem", "Điểm")):
        if k in m:
            return name
    return None


def build_outline(lines: list[dict], labels: list[str]) -> list[dict]:
    order = {"Phần": 0, "Chương": 1, "Mục": 2, "Điều": 3, "Khoản": 4, "Điểm": 5}
    own_kinds = ["OWN_PHAN", "OWN_CHUONG", "OWN_MUC", "OWN_DIEU", "OWN_KHOAN", "OWN_DIEM"]
    present = [k for k in own_kinds if k in labels]
    own_level = {k: i + 1 for i, k in enumerate(present)}      # bố cục thực có của văn bản (mục 1 nguyentac.md)
    heads: list[dict] = []
    anchor = 0
    q_stack: list[int] = []
    for i, (ln, lab) in enumerate(zip(lines, labels)):
        if lab in own_level:
            text = ln["text"]
            for nxt, nl in zip(lines[i + 1:i + 3], labels[i + 1:i + 3]):
                if nl != "BODY":
                    break
                nt = nxt["text"]
                letters = [c for c in nt if c.isalpha()]
                is_caps = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.9
                if lab in ("OWN_PHAN", "OWN_CHUONG", "OWN_MUC"):
                    if not is_caps:                    # tên Phần/Chương/Mục là dòng viết HOA ngay sau
                        break
                elif lab == "OWN_DIEU":
                    if re.search(r"[.;:]$", text) or not nt[:1].islower():   # chỉ nối tên điều bị xuống dòng
                        break
                else:
                    break
                text += " " + nt
            heads.append({"level": own_level[lab], "page": ln["page"], "text": text, "kind": lab})
            anchor, q_stack = own_level[lab], []
        elif lab == "QUOTED":
            u = quoted_unit(ln["m"])
            if not u:
                continue
            while q_stack and q_stack[-1] >= order[u]:
                q_stack.pop()
            q_stack.append(order[u])
            heads.append({"level": anchor + len(q_stack), "page": ln["page"], "text": ln["text"], "kind": f"QUOTED_{u}"})
    return heads


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default="12")
    ap.add_argument("--beam", type=int, default=64)
    args = ap.parse_args()
    t0 = time.perf_counter()
    pages = json.loads((WORK / "ocr" / args.doc / "pages.json").read_text(encoding="utf-8"))
    lines, dropped = [], 0
    for p in sorted(pages, key=int):
        for ln in pages[p]["lines"]:
            text = norm(ln["text"])
            if not text or any(j.match(text) for j in JUNK):
                dropped += 1
                continue
            lines.append({"page": int(p), "text": text, "m": markers(text)})
    labels, ambiguous = decode(lines, args.beam)
    heads = build_outline(lines, labels)
    sec = time.perf_counter() - t0

    out = WORK / "grammar" / args.doc
    out.mkdir(parents=True, exist_ok=True)
    (out / "lines.json").write_text(json.dumps([{"page": l["page"], "label": lab, "text": l["text"]}
                                                for l, lab in zip(lines, labels)], ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "outline.json").write_text(json.dumps(heads, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# Dàn ý - grammar ({len(heads)} tiêu đề)", ""] + \
         [f"{'  ' * (h['level'] - 1)}- [L{h['level']}, tr.{h['page']}] {h['text'][:140]}" for h in heads]
    (out / "outline_grammar.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    shutil.copy(out / "outline_grammar.md", WORK / "compare" / args.doc / "outline_grammar.md")
    from collections import Counter
    stats = {"lines": len(lines), "dropped_junk_lines": dropped, "labels": dict(Counter(labels)),
             "headings": len(heads), "by_kind": dict(Counter(h["kind"] for h in heads)),
             "by_level": dict(sorted(Counter(h["level"] for h in heads).items())),
             "ambiguous_lines": len(ambiguous), "ambiguous": ambiguous[:40], "seconds": round(sec, 2), "beam": args.beam,
             "own_articles": [h["text"][:90] for h in heads if h["kind"] == "OWN_DIEU"],
             "own_groups": [h["text"][:90] for h in heads if h["kind"] in ("OWN_PHAN", "OWN_CHUONG", "OWN_MUC")]}
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "ambiguous"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
