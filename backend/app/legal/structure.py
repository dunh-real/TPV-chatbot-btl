"""Cây Phần > Chương > Mục > Điều > Khoản > Điểm của một văn bản, từ Markdown của converter.

Một nguồn sự thật cho cấu trúc: `scan()` gọi `legal_grammar.parse_legal` một lần và trả về
các dòng tiêu đề của CHÍNH văn bản (đơn vị trong phạm vi trích dẫn của luật sửa đổi là thân
của lệnh sửa đổi, không phải đơn vị của văn bản này). Chunker (`app.rag.chunking`) và cây
phiên bản (`app.legal.service`) cùng dùng nó, nên ranh giới chunk và ranh giới component
luôn khớp nhau.

Mỗi nút giữ chữ CỦA RIÊNG nó (dòng tiêu đề + thân trước đơn vị con đầu tiên); `render`
ghép lại theo thứ tự cây để dựng lại văn bản tại một thời điểm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documents.legal_grammar import LegalHeading, LegalParse, norm as legal_norm, parse_legal

LEVEL = {"Mở đầu": -1, "Phần": 0, "Chương": 1, "Mục": 2, "Tiểu mục": 3, "Điều": 4, "Khoản": 5, "Điểm": 6,
         "Phụ lục": 0}
OWN_HEAD_KINDS = ("OWN_PHAN", "OWN_CHUONG", "OWN_MUC", "OWN_DIEU", "OWN_KHOAN", "OWN_DIEM", "ANNEX")

# Dấu Markdown bọc dòng: heading, in đậm/nghiêng ("Điều 43*.* Thu, chi" từ MarkItDown), trích dẫn.
_MD_DECOR = re.compile(r"^\s*#{1,6}\s*|\*+|^\s*>\s?")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_DIEU_HEAD = re.compile(r"^Điều\s+\d+[a-z]?\.\s*\S")
_NUM = {
    "Phần": re.compile(r"^PHẦN\s+(?:THỨ\s+)?(\S+)", re.I),
    "Chương": re.compile(r"^CHƯƠNG\s+([IVXLC]+)", re.I),
    "Mục": re.compile(r"^MỤC\s+(\d+)", re.I),
    "Điều": re.compile(r"^Điều\s+(\d+[a-z]?)"),
    "Khoản": re.compile(r"^(\d{1,3}[a-z]?)\."),
    "Điểm": re.compile(r"^([a-zđ]\d?)\)"),
}
_SLUG_UNIT = {"Phần": "phan", "Chương": "chuong", "Mục": "muc", "Điều": "dieu", "Khoản": "khoan",
              "Điểm": "diem", "Phụ lục": "phu-luc", "Mở đầu": "mo-dau"}


def plain_line(line: str) -> str:
    return legal_norm(_MD_DECOR.sub("", line))


@dataclass(slots=True)
class Scan:
    lines: list[str]
    plain: dict[int, str]
    heads: dict[int, LegalHeading]       # chỉ số dòng -> tiêu đề của chính văn bản
    consumed: set[int]                   # dòng tên Phần/Chương/Mục đã nối vào tiêu đề
    parsed: LegalParse


def scan(text: str, units: tuple[str, ...] = OWN_HEAD_KINDS) -> Scan | None:
    """Tiêu đề của chính văn bản theo legal_grammar; None nếu không phải VBQPPL."""
    lines = text.split("\n")
    idx = [i for i, ln in enumerate(lines) if ln.strip()]
    plain = {i: plain_line(lines[i]) for i in idx}
    parsed = parse_legal(["" if _TABLE_LINE.match(lines[i]) else plain[i] for i in idx])
    if parsed is None:
        return None
    heads = {idx[h.index]: h for h in parsed.headings if h.kind in units}
    if not any(h.unit == "Điều" for h in heads.values()):
        return None
    # Bộ giải mã phạt bước nhảy số Điều (dành cho OCR rơi Điều) nặng hơn nhãn BODY, nên
    # văn bản trích một phần ("Điều 1", "Điều 4", "Điều 20") mất ranh giới. Dòng "Điều n."
    # nhãn BODY - tức KHÔNG ở trong phạm vi trích dẫn (ở đó nhãn là QUOTED) - vẫn là Điều.
    for k, i in enumerate(idx):
        if i not in heads and parsed.labels[k] == "BODY" and _DIEU_HEAD.match(plain[i]):
            heads[i] = LegalHeading(k, 0, "OWN_DIEU", plain[i], "Điều")

    consumed: set[int] = set()
    for i, h in heads.items():
        if h.unit not in ("Phần", "Chương", "Mục"):
            continue
        j, taken = i + 1, 0
        while j < len(lines) and taken < 2:
            if not lines[j].strip():
                j += 1
                continue
            if j not in heads and plain.get(j) and plain[j] in h.text:
                consumed.add(j)
                taken += 1
                j += 1
            else:
                break
    return Scan(lines=lines, plain=plain, heads=heads, consumed=consumed, parsed=parsed)


def unit_number(unit: str, text: str) -> str:
    pattern = _NUM.get(unit)
    if pattern is None:
        return ""
    m = pattern.match(text.lstrip("“\"‘ "))
    if m is None:
        return ""
    num = m.group(1)
    return num.upper() if unit == "Chương" else num.lower()


@dataclass(slots=True)
class Node:
    component_id: str
    parent_id: str | None
    unit: str
    so: str
    thu_tu: float
    path: str
    lines: list[str] = field(default_factory=list)
    line: int = -1          # chỉ số dòng tiêu đề trong Markdown nguồn (-1: nút dựng từ CSDL)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def make_id(base: str, unit: str, so: str) -> str:
    from app.legal.identity import slug

    # "Phần thứ nhất" -> phan-nhat; số/chữ cái của Điều, Khoản, Điểm giữ nguyên ("đ").
    so = slug(so) if unit == "Phần" else so.lower()
    return f"{base}:{_SLUG_UNIT[unit]}-{so}" if so else f"{base}:{_SLUG_UNIT[unit]}"


def label(unit: str, so: str) -> str:
    return f"{unit} {so}".strip()


def build_tree(text: str, legal_id: str) -> list[Node]:
    """Cây component theo thứ tự đọc; [] nếu không phải VBQPPL."""
    sc = scan(text)
    if sc is None:
        return []
    nodes: list[Node] = []
    seen: dict[str, int] = {}
    preamble = Node(make_id(legal_id, "Mở đầu", ""), None, "Mở đầu", "", 0.0, "Mở đầu")
    nodes.append(preamble)
    stack: list[Node] = []
    annex_count = 0
    for i, line in enumerate(sc.lines):
        if i in sc.consumed:
            # tên Phần/Chương/Mục: thuộc chữ của chính nút đó
            if stack:
                stack[-1].lines.append(line)
            continue
        h = sc.heads.get(i)
        if h is None:
            (stack[-1] if stack else preamble).lines.append(line)
            continue
        unit = h.unit
        if unit == "Phụ lục":
            annex_count += 1
            so = str(annex_count)
        else:
            so = unit_number(unit, sc.plain[i]) or str(sum(1 for n in nodes if n.unit == unit) + 1)
        while stack and LEVEL[stack[-1].unit] >= LEVEL[unit]:
            stack.pop()
        if unit == "Phụ lục":
            stack = []
        parent = stack[-1] if stack else None
        # Điều đánh số liên tục cả văn bản -> id không phụ thuộc Chương; Khoản/Điểm theo cha.
        # Mục đánh lại trong mỗi Chương -> id theo Chương.
        if unit in ("Điều", "Phần", "Chương", "Phụ lục"):
            base = legal_id
        else:
            base = parent.component_id if parent else legal_id
        cid = make_id(base, unit, so)
        if cid in seen:
            seen[cid] += 1
            cid = f"{cid}~{seen[cid]}"       # nguồn lặp nhãn: giữ cả hai, không ghi đè
        else:
            seen[cid] = 1
        path = " > ".join(p for p in ((parent.path if parent else ""), label(unit, so)) if p)
        node = Node(cid, parent.component_id if parent else None, unit, so, float(len(nodes)), path, [line],
                    line=i)
        nodes.append(node)
        stack.append(node)
    return nodes


def children_map(nodes: list[Node]) -> dict[str | None, list[Node]]:
    out: dict[str | None, list[Node]] = {}
    for n in nodes:
        out.setdefault(n.parent_id, []).append(n)
    for kids in out.values():
        kids.sort(key=lambda n: n.thu_tu)
    return out


def render(nodes: list[Node], texts: dict[str, str] | None = None) -> str:
    """Ghép chữ theo thứ tự cây. `texts` (component_id -> chữ) chọn phiên bản; nút không
    có trong `texts` thì bỏ (đã hết hiệu lực tại thời điểm render)."""
    kids = children_map(nodes)
    out: list[str] = []

    def walk(parent: str | None) -> None:
        for n in kids.get(parent, []):
            body = n.text if texts is None else texts.get(n.component_id)
            if body is None:
                continue
            if body.strip():
                out.append(body.strip())
            walk(n.component_id)

    walk(None)
    return "\n\n".join(out)


def subtree_text(nodes: list[Node], root_id: str, texts: dict[str, str]) -> str:
    """Chữ của một đơn vị kèm toàn bộ đơn vị con (dùng khi tra thẳng theo địa chỉ)."""
    kids = children_map(nodes)
    out: list[str] = []

    def walk(cid: str) -> None:
        body = texts.get(cid)
        if body is None:
            return
        if body.strip():
            out.append(body.strip())
        for k in kids.get(cid, []):
            walk(k.component_id)

    walk(root_id)
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
# Đoạn trích dẫn trong lệnh sửa đổi
# --------------------------------------------------------------------------- #
_OPEN = re.compile(r"^\s*[“\"‘]\s*")
_CLOSE = re.compile(r"\s*[”\"’]\s*[.;,]?\s*(?:\./\.)?\s*$")


def strip_quotes(lines: list[str]) -> list[str]:
    """Bỏ ngoặc mở ở dòng đầu và ngoặc đóng ("”." / "”;") ở dòng cuối của đoạn trích."""
    out = [ln for ln in lines if ln.strip()]
    if not out:
        return out
    out[0] = _OPEN.sub("", out[0], count=1)
    out[-1] = _CLOSE.sub("", out[-1])
    return out


@dataclass(slots=True)
class Fragment:
    unit: str
    so: str
    lines: list[str]
    children: list["Fragment"] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.lines).strip()


def parse_fragment(lines: list[str], top_unit: str) -> Fragment | None:
    """Nội dung mới của một Điều/Khoản/Điểm (đã bỏ ngoặc) -> cây Fragment.

    Đoạn trích là văn bản đúng thể thức nên chỉ cần nhận mở đầu dòng: dưới Điều là Khoản
    "n." (hoặc Điểm "x)" nếu Điều không chia Khoản), dưới Khoản là Điểm.
    """
    lines = [plain_line(ln) for ln in lines if ln.strip()]
    if not lines:
        return None
    top = Fragment(top_unit, unit_number(top_unit, lines[0]), [lines[0]])
    current: Fragment | None = None       # Khoản đang mở (khi top là Điều)
    for line in lines[1:]:
        if top_unit == "Điều" and _NUM["Khoản"].match(line) and re.match(r"^\d{1,3}[a-z]?\.\s", line):
            current = Fragment("Khoản", unit_number("Khoản", line), [line])
            top.children.append(current)
        elif top_unit in ("Điều", "Khoản") and re.match(r"^[a-zđ]\d?\)\s", line):
            diem = Fragment("Điểm", unit_number("Điểm", line), [line])
            (current if current is not None else top).children.append(diem)
        elif current is not None and current.children:
            current.children[-1].lines.append(line)
        elif current is not None:
            current.lines.append(line)
        elif top.children:
            top.children[-1].lines.append(line)
        else:
            top.lines.append(line)
    return top
