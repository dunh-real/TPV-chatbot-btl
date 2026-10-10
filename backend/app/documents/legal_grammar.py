"""Dàn ý văn bản quy phạm bằng văn phạm + giải mã có ràng buộc (không LLM).

Bố cục Phần > Chương > Mục > Điều > Khoản > Điểm, quy tắc đánh số và quy tắc văn bản
sửa đổi theo `tests/layout_experiment/nguyentac.md`. Khác cách suy theo ký hiệu đánh
số ở `outline.py` ở chỗ quyết định trên CẢ văn bản chứ không từng dòng một:

  1. Mỗi khối có tập nhãn khả dĩ từ dạng mở đầu (Phần/Chương/Mục/Điều n/Khoản k/Điểm x).
  2. Beam search chọn chuỗi nhãn rẻ nhất: dãy số phải tăng liền (Điều chạy suốt văn
     bản, Khoản trong một Điều, Điểm a b c d đ e g... trong một Khoản); Khoản/Điểm của
     văn bản kết thúc "như sau:" MỞ phạm vi trích dẫn, đơn vị kế tiếp của văn bản ĐÓNG nó.
  3. Nội dung trích dẫn (điều/khoản/điểm được sửa đổi) lồng dưới lệnh sửa đổi, không
     bị hiểu là Điều của chính văn bản.

Không nhận ra đơn vị nào của chính văn bản thì trả None: tài liệu không phải văn bản
quy phạm, `outline.py` quay về cách cũ. Thực nghiệm so sánh với cách cũ:
`REVIEW-TRICH-XUAT-LAYOUT.md`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

VN_LETTERS = "abcdđeghiklmnopqrstuvxy"
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_to_int(text: str) -> int:
    """Số La Mã -> số nguyên; 0 nếu không phải số La Mã. Không giới hạn ở XX: Bộ luật
    Hình sự có tới Chương XXVI, bảng tra cứng cũ làm rơi cả sáu chương cuối."""
    total = prev = 0
    for ch in reversed(text.upper()):
        value = _ROMAN_VALUES.get(ch)
        if value is None:
            return 0
        total, prev = (total - value, prev) if value < prev else (total + value, value)
    return total
ORDINAL_WORDS = {"NHẤT": 1, "HAI": 2, "BA": 3, "TƯ": 4, "BỐN": 4, "NĂM": 5, "SÁU": 6, "BẢY": 7, "TÁM": 8,
                 "CHÍN": 9, "MƯỜI": 10}

# Dòng rác khi in văn bản từ trình duyệt: ngày giờ in, about:blank, số trang "n/N".
JUNK = [re.compile(p) for p in (r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*(AM|PM)?$", r"^about:blank$",
                                r"^\d{1,3}/\d{1,3}$", r"^about:blank\s+\d{1,3}/\d{1,3}$")]
QUOTE_OPEN = ("“", '"', "‘")
AMEND_VERB = re.compile(r"\b(sửa đổi|bổ sung|bãi bỏ|thay thế|thay cụm từ|bỏ cụm từ|đính chính|thay đổi)\b", re.I)
AS_FOLLOWS = re.compile(r"như sau\s*:?\s*[“\"]?\s*$|như sau\s*:\s*[“\"]", re.I)

RE_PHAN = re.compile(r"^PHẦN\s+(THỨ\s+\S+|[IVXLC]+)\b", re.I)
RE_CHUONG = re.compile(r"^CHƯƠNG\s+([IVXLC]+)\b", re.I)
RE_ANNEX = re.compile(r"^(?:PHỤ LỤC|Phụ lục)\b")
QUOTE_CLOSE = re.compile(r"[”\"’]\s*[.;,]?\s*(?:\./\.)?\s*$")
RE_MUC = re.compile(r"^MỤC\s+(\d+)\b", re.I)
RE_DIEU = re.compile(r"^Điều\s+(\d+)([a-z]?)\.\s*\S")
RE_KHOAN = re.compile(r"^(\d{1,2})([a-z]?)\.\s+\S")
RE_DIEM = re.compile(rf"^([{VN_LETTERS}])(\d?)\)\s+\S")

DEFAULT_BEAM = 64
OWN_KINDS = ("OWN_PHAN", "OWN_CHUONG", "OWN_MUC", "OWN_DIEU", "OWN_KHOAN", "OWN_DIEM")
UNIT_ORDER = {"Phần": 0, "Chương": 1, "Mục": 2, "Điều": 3, "Khoản": 4, "Điểm": 5}


@dataclass(slots=True)
class LegalHeading:
    index: int              # vị trí trong danh sách dòng đầu vào
    level: int
    kind: str               # OWN_DIEU, QUOTED_Khoản, ...
    text: str               # đã nối tên Chương/Điều bị xuống dòng
    unit: str               # Phần | Chương | Mục | Điều | Khoản | Điểm


@dataclass(slots=True)
class LegalParse:
    headings: list[LegalHeading]
    labels: list[str]       # nhãn từng dòng; dòng rác mang nhãn "JUNK"
    ambiguous: list[dict]   # dòng mà hai giả thuyết tốt nhất gần ngang điểm


def norm(text: str) -> str:
    # "Ð" (U+00D0, chữ Eth) hay bị gõ nhầm cho "Đ" (U+0110): nhìn y hệt nhưng mọi mẫu
    # "^Điều" trượt qua - Bộ luật Hình sự mất hẳn Điều 317 vì một ký tự như vậy.
    text = unicodedata.normalize("NFC", text).replace("Ð", "Đ").replace("ð", "đ")
    return " ".join(text.replace("\xa0", " ").split())


def is_junk(text: str) -> bool:
    return any(j.match(text) for j in JUNK)


def markers(text: str) -> dict:
    """Dạng mở đầu của dòng (bỏ dấu ngoặc kép mở để nhận cả đơn vị trong trích dẫn)."""
    quoted = text.startswith(QUOTE_OPEN)
    t = text.lstrip("“\"‘ ").strip()
    m = {"quoted": quoted, "as_follows": bool(AS_FOLLOWS.search(text)), "amend_verb": bool(AMEND_VERB.search(t[:120]))}
    if (x := RE_PHAN.match(t)):
        g = x.group(1).upper()
        m["phan"] = roman_to_int(g) or ORDINAL_WORDS.get(g.split()[-1], 0)
    if (x := RE_CHUONG.match(t)):
        m["chuong"] = roman_to_int(x.group(1))
    if (x := RE_MUC.match(t)):
        m["muc"] = int(x.group(1))
    if (x := RE_DIEU.match(t)):
        m["dieu"] = (int(x.group(1)), x.group(2))
    if (x := RE_KHOAN.match(t)):
        m["khoan"] = (int(x.group(1)), x.group(2))
    if (x := RE_DIEM.match(t)):
        m["diem"] = (VN_LETTERS.index(x.group(1)) + 1, x.group(2))
    return m


@dataclass
class _Hyp:
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


def _step(cur: int, n: int, suf: str) -> float | None:
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


def _options(h: _Hyp, m: dict, text: str) -> list[tuple[str, float, dict]]:
    """Các nhãn khả dĩ cho dòng hiện tại dưới giả thuyết h: (nhãn, chi phí, thay đổi trạng thái)."""
    out = []
    structural = any(k in m for k in ("dieu", "khoan", "diem", "phan", "chuong", "muc"))
    # Luật cứng: dòng mở bằng ngoặc kép, dòng nằm giữa một cặp ngoặc đã ghép (in_quote) và
    # dòng thuộc phụ lục sau Điều cuối đều không thể là đơn vị của chính văn bản.
    own_ok = not m["quoted"] and not m.get("in_quote") and not m.get("annex")
    if m.get("annex"):
        return [("BODY", 0.0, {})]

    # Mục đánh lại trong mỗi Chương; số Điều chạy liên tục qua các Chương nên không đặt lại.
    # Sau một đơn vị lớn, Khoản/Điểm chỉ hợp lệ khi đã có Điều mới.
    if own_ok and m.get("phan"):
        c = _step(h.phan, m["phan"], "")
        if c is not None:
            out.append(("OWN_PHAN", c, {"phan": m["phan"], "chuong": h.chuong, "muc": 0, "khoan": 0, "diem": 0,
                                        "scope": False, "seen_dieu": False}))
    if own_ok and m.get("chuong"):
        c = _step(h.chuong, m["chuong"], "")
        if c is not None:
            out.append(("OWN_CHUONG", c, {"chuong": m["chuong"], "muc": 0, "khoan": 0, "diem": 0,
                                          "scope": False, "seen_dieu": False}))
    if own_ok and "muc" in m:
        c = _step(h.muc, m["muc"], "")
        if c is not None:
            out.append(("OWN_MUC", c, {"muc": m["muc"], "khoan": 0, "diem": 0, "scope": False, "seen_dieu": False}))
    if own_ok and "dieu" in m:
        n, suf = m["dieu"]
        c = _step(h.dieu, n, suf)
        if c is not None and suf and h.scope:
            c += 3.0                        # "Điều 4b." trong phạm vi trích dẫn: gần như chắc là điều được chèn
        if c is not None:
            out.append(("OWN_DIEU", c, {"dieu": n, "khoan": 0, "diem": 0, "scope": False, "seen_dieu": True,
                                        "dieu_amending": bool(AMEND_VERB.search(text))}))
    if own_ok and "khoan" in m and h.seen_dieu:
        k, suf = m["khoan"]
        c = _step(h.khoan, k, suf)
        if c is not None:
            # Trong Điều sửa đổi, Khoản của chính văn bản phải là một lệnh sửa đổi.
            c += 3.0 if h.dieu_amending and not m["amend_verb"] else 0.0
            out.append(("OWN_KHOAN", c, {"khoan": k, "diem": 0, "scope": m["as_follows"]}))
    if own_ok and "diem" in m and h.seen_dieu and h.khoan > 0:
        d, suf = m["diem"]
        c = _step(h.diem, d, suf)
        if c is not None:
            c += 3.0 if h.dieu_amending and not m["amend_verb"] else 0.0
            out.append(("OWN_DIEM", c, {"diem": d, "scope": m["as_follows"]}))
    if h.scope or m.get("in_quote"):
        # Trong phạm vi trích dẫn mọi dòng đều có thể là nội dung được sửa đổi. Dòng có đánh số
        # đắt hơn chút để "của văn bản" thắng khi dãy số khớp VÀ có động từ sửa đổi.
        out.append(("QUOTED", 0.5 if structural else 0.0, {}))
    body_cost = 2.5 if structural else 0.0
    if h.scope and structural:
        body_cost = 6.0                     # trong trích dẫn đã có nhãn QUOTED rẻ hơn
    out.append(("BODY", body_cost, {}))
    return out


def _decode(lines: list[dict], beam: int) -> tuple[list[str], list[dict]]:
    hyps = [_Hyp(0.0)]
    ambiguous = []
    for i, ln in enumerate(lines):
        m = ln["m"]
        cand: dict[tuple, _Hyp] = {}
        for h in hyps:
            for label, cost, upd in _options(h, m, ln["text"]):
                nh = _Hyp(h.score + cost, h.phan, h.chuong, h.muc, h.dieu, h.dieu_amending, h.khoan, h.diem,
                          h.scope, h.seen_dieu, h.labels + [label])
                for k, v in upd.items():
                    setattr(nh, k, v)
                kk = nh.key()
                if kk not in cand or cand[kk].score > nh.score:
                    cand[kk] = nh
        hyps = sorted(cand.values(), key=lambda x: x.score)[:beam]
        if len(hyps) > 1 and hyps[0].labels[-1] != hyps[1].labels[-1] and hyps[1].score - hyps[0].score < 1.0:
            ambiguous.append({"index": ln["index"], "text": ln["text"][:100],
                              "best": hyps[0].labels[-1], "second": hyps[1].labels[-1],
                              "gap": round(hyps[1].score - hyps[0].score, 2)})
    return hyps[0].labels, ambiguous


def _quoted_unit(m: dict) -> str | None:
    for k, name in (("phan", "Phần"), ("chuong", "Chương"), ("muc", "Mục"), ("dieu", "Điều"),
                    ("khoan", "Khoản"), ("diem", "Điểm")):
        if k in m:
            return name
    return None


def _headings(lines: list[dict], labels: list[str]) -> list[LegalHeading]:
    present = [k for k in OWN_KINDS if k in labels]
    own_level = {k: i + 1 for i, k in enumerate(present)}      # bố cục thực có của văn bản
    unit_of = {"OWN_PHAN": "Phần", "OWN_CHUONG": "Chương", "OWN_MUC": "Mục", "OWN_DIEU": "Điều",
               "OWN_KHOAN": "Khoản", "OWN_DIEM": "Điểm"}
    heads: list[LegalHeading] = []
    anchor = 0
    q_stack: list[int] = []
    for i, (ln, lab) in enumerate(zip(lines, labels)):
        if ln["m"].get("annex_start"):
            heads.append(LegalHeading(ln["index"], 1, "ANNEX", ln["text"], "Phụ lục"))
            anchor, q_stack = 1, []
            continue
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
            heads.append(LegalHeading(ln["index"], own_level[lab], lab, text, unit_of[lab]))
            anchor, q_stack = own_level[lab], []
        elif lab == "QUOTED":
            u = _quoted_unit(ln["m"])
            if not u:
                continue
            while q_stack and q_stack[-1] >= UNIT_ORDER[u]:
                q_stack.pop()
            q_stack.append(UNIT_ORDER[u])
            heads.append(LegalHeading(ln["index"], anchor + len(q_stack), f"QUOTED_{u}", ln["text"], u))
    return heads


def _pair_quotes(lines: list[dict], max_span: int = 400) -> None:
    """Ghép cặp ngoặc kép trên CẢ văn bản trước khi giải mã, đánh `in_quote` cho các dòng
    nằm giữa một dòng mở “ và dòng đóng ” tương ứng.

    Đếm ngoặc lần lượt từng dòng thì một dấu lạc làm sai cách đọc của mọi thứ phía sau.
    Ở đây một dòng mở chỉ được ghép với dòng đóng gần nhất KHÔNG vượt qua một dòng mở
    khác; không ghép được thì chỉ chính dòng đó là trích dẫn - dấu lạc chỉ tự hại mình.
    """
    i = 0
    while i < len(lines):
        if not lines[i]["m"]["quoted"]:
            i += 1
            continue
        end = None
        for j in range(i, min(len(lines), i + max_span)):
            if j > i and lines[j]["m"]["quoted"]:
                break
            if QUOTE_CLOSE.search(lines[j]["text"]):
                end = j
                break
        if end is None:
            i += 1
            continue
        for k in range(i + 1, end + 1):
            lines[k]["m"]["in_quote"] = True
        i = end + 1


def _mark_annex(lines: list[dict]) -> None:
    """Phụ lục sau Điều cuối có đánh số riêng ("1.", "a)", "II.") nhưng không phải Khoản/Điểm
    của Điều cuối - để lẫn vào thì nhãn trích dẫn phụ lục thành "Điều 3" (Thông tư 76/2026)."""
    last_dieu = max((k for k, ln in enumerate(lines)
                     if "dieu" in ln["m"] and not ln["m"]["quoted"] and not ln["m"].get("in_quote")),
                    default=None)
    if last_dieu is None:
        return
    start = next((k for k in range(last_dieu + 1, len(lines)) if RE_ANNEX.match(lines[k]["text"])), None)
    if start is None:
        return
    for k in range(start, len(lines)):
        lines[k]["m"]["annex"] = True
    lines[start]["m"]["annex_start"] = True


def parse_legal(texts: list[str], beam: int = DEFAULT_BEAM) -> LegalParse | None:
    """Dựng dàn ý văn bản quy phạm từ các dòng/khối theo thứ tự đọc.

    `texts[i]` là chữ của khối thứ i; `LegalHeading.index` trỏ lại đúng vị trí đó.
    Trả None khi không nhận ra đơn vị nào của chính văn bản.
    """
    lines = []
    labels = ["JUNK"] * len(texts)
    for index, raw in enumerate(texts):
        text = norm(raw)
        if not text or is_junk(text):
            continue
        lines.append({"index": index, "text": text, "m": markers(text)})
    if not lines:
        return None
    _pair_quotes(lines)
    _mark_annex(lines)
    line_labels, ambiguous = _decode(lines, beam)
    if not any(lab in OWN_KINDS for lab in line_labels):
        return None
    for ln, lab in zip(lines, line_labels):
        labels[ln["index"]] = lab
    return LegalParse(headings=_headings(lines, line_labels), labels=labels, ambiguous=ambiguous)
