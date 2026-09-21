"""Dàn ý của tài liệu: tiêu đề, các mục, thứ bậc - đọc tất định từ cây khối.

Đây là đầu vào cho rule engine, và cũng là thứ trả về giao diện để người đọc thấy
hệ thống hiểu tài liệu được tổ chức ra sao.

Hai nguồn cho một tiêu đề mục, xếp theo độ tin cậy:
    1. style "Heading N" của DOCX, "###" của Markdown - người soạn khai hẳn cấp mục
    2. dòng NGẮN mở đầu bằng đánh số: "I.", "1.", "1.2.", "a)", "Chương II"

Nguồn 2 chỉ dùng khi tài liệu không khai đủ bằng style - đã khai thì tin cái đã
khai. "Đủ" ở đây là từ hai mục trở lên: một style Heading duy nhất thường chỉ là
cái tên đặt cho tài liệu, các mục bên trong vẫn đánh số tay. Không có nguồn nào thì
dàn ý rỗng và rule engine báo "chưa kiểm được", chứ không đoán bừa rồi báo lỗi oan.

Cấp của mục đánh số tay KHÔNG suy từ kiểu đánh số - "I." không nghiễm nhiên là cấp
1, mỗi nơi một thói quen. Ở đây cấp được cấp phát theo thứ tự xuất hiện: kiểu đánh
số nào gặp trước thì nông hơn. Nhờ vậy dàn ý phản ánh đúng tài liệu đang đọc chứ
không phải thói quen của người viết code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documents.parser import Block, DocumentStructure

_STYLE_LEVEL = re.compile(r"heading\s*(\d)", re.I)

# Bảng chữ cái tiếng Việt cho danh sách "a) b) c)", đúng 23 chữ dùng làm số thứ tự
# trong văn bản hành chính: có "đ", KHÔNG có f/j/w/z. Để lọt "f" vào đây thì danh
# sách đúng chuẩn "... e) g) h)" bị đọc thành 6, 8, 9 và bị kết luận là đứt quãng -
# lỗi oan trên đúng thứ mà Nghị định 30 quy định. Vẫn giữ "đ" dù nhiều danh sách
# nhảy thẳng từ d) sang e): chỗ kiểm tính liên tục chấp nhận cả hai.
LETTERS = "abcdđeghiklmnopqrstuvxy"

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Dòng mở đầu bằng đánh số. Bắt buộc có dấu phân cách rồi mới tới khoảng trắng:
# thiếu nó thì "2026 là năm..." cũng thành mục số 2026. Mỗi phần số tối đa hai
# chữ số, nên năm và số tiền không lọt vào đây.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("word", re.compile(r"^(?P<word>Phần|Chương|Mục|Điều|Bài)\s+(?P<num>\d{1,2}|[IVXLCDM]{1,6})\b", re.I)),
    ("roman", re.compile(r"^(?P<num>[IVXLCDM]{1,6})\s*[.)]\s+(?=\S)")),
    ("digit", re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2})*)\s*[.)]\s+(?=\S)")),
    ("letter", re.compile(r"^(?P<num>[a-zđ])\s*[.)]\s+(?=\S)", re.I)),
)

# Quá số này thì gần như chắc chắn đã bắt nhầm năm hoặc số liệu, không phải số mục.
MAX_ORDINAL = 99


@dataclass(slots=True)
class Marker:
    """Một dòng mở đầu bằng đánh số - dùng để kiểm tính liên tục."""

    block_id: str
    index: int              # vị trí trong danh sách khối không rỗng
    kind: str               # word | roman | digit | letter
    key: str                # nhóm so sánh: "digit:2", "word:chương"
    ordinal: int
    marker: str             # đúng chuỗi đánh số đọc được: "2.", "b)", "Chương II"
    text: str


@dataclass(slots=True)
class Heading:
    block_id: str
    index: int
    level: int
    text: str
    marker: str = ""


@dataclass(slots=True)
class Outline:
    blocks: list[Block] = field(default_factory=list)      # khối không rỗng, đúng thứ tự
    headings: list[Heading] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    title: Heading | None = None
    explicit: bool = False        # True: dàn ý lấy từ style/Markdown, không phải suy ra

    @property
    def heading_ids(self) -> set[str]:
        ids = {h.block_id for h in self.headings}
        if self.title is not None:
            ids.add(self.title.block_id)
        return ids


def _roman_to_int(text: str) -> int | None:
    total = 0
    previous = 0
    for char in reversed(text.upper()):
        value = _ROMAN_VALUES.get(char)
        if value is None:
            return None
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total or None


def parse_marker(text: str) -> Marker | None:
    """Đọc phần đánh số ở đầu một khối; None nghĩa là khối không đánh số."""
    head = text.strip()
    for kind, pattern in _MARKERS:
        match = pattern.match(head)
        if match is None:
            continue

        raw = match.group("num")
        if kind == "letter":
            ordinal = LETTERS.find(raw.lower()) + 1
        elif kind == "roman" or (kind == "word" and not raw.isdigit()):
            ordinal = _roman_to_int(raw) or 0
        else:
            ordinal = int(raw.rsplit(".", 1)[-1])

        # Ngoài khoảng thì THỬ TIẾP kiểu sau, đừng bỏ hẳn khối. "C." và "D." khớp
        # pattern La Mã trước (100 và 500), quá MAX_ORDINAL, mà bỏ ở đây thì mục
        # "C." "D." biến mất khỏi dàn ý và dãy A) B) C) D) trông như bị đứt.
        # Rơi xuống pattern "letter" là đọc được đúng chữ cái.
        if not 1 <= ordinal <= MAX_ORDINAL:
            continue

        if kind == "digit":
            key = f"digit:{raw.count('.') + 1}"
        elif kind == "word":
            key = f"word:{match.group('word').lower()}"
        else:
            key = kind

        return Marker(block_id="", index=0, kind=kind, key=key, ordinal=ordinal,
                      marker=match.group(0).strip(), text=head)
    return None


def _style_level(block: Block) -> int | None:
    if block.kind != "heading":
        return None
    match = _STYLE_LEVEL.search(block.style or "")
    return int(match.group(1)) if match else 1


def _looks_like_heading(text: str, max_chars: int) -> bool:
    """Dòng ngắn, một dòng, không kết thúc như một câu.

    Đoạn nội dung có đánh số ("1. Trong tháng 8, đơn vị đã hoàn thành...") không
    phải tiêu đề mục. Nhận nhầm thì mọi đoạn đánh số đều thành một mục rỗng.
    """
    clean = text.strip()
    return bool(clean) and "\n" not in clean and len(clean) <= max_chars \
        and not clean.endswith((".", ";", ","))


def _marker_headings(markers: list[Marker], max_chars: int) -> list[Heading]:
    levels: dict[str, int] = {}
    headings: list[Heading] = []
    for marker in markers:
        if not _looks_like_heading(marker.text, max_chars):
            continue
        level = levels.setdefault(marker.key, min(len(levels) + 1, 6))
        headings.append(Heading(marker.block_id, marker.index, level,
                                marker.text, marker.marker))
    return headings


def _title(blocks: list[Block], headings: list[Heading], max_chars: int) -> Heading | None:
    """Tiêu đề mở đầu tài liệu, tìm trong vài khối đầu.

    Không soi đúng khối đầu tiên vì rất nhiều tài liệu mở đầu bằng một bảng bố cục
    (logo, tên cơ quan, quốc hiệu); tiêu đề thật nằm ngay sau đó.
    """
    for index, block in enumerate(blocks[:3]):
        if block.kind == "table":
            continue
        found = next((h for h in headings if h.block_id == block.id), None)
        if found is not None:
            return found
        if (level := _style_level(block)) is not None:
            return Heading(block.id, index, level, block.text.strip())
        letters = [c for c in block.text if c.isalpha()]
        hoa = bool(letters) and all(c.isupper() for c in letters)
        if _looks_like_heading(block.text, max_chars) and (block.bold or hoa):
            return Heading(block.id, index, 1, block.text.strip())
    return None


def build_outline(
    structure: DocumentStructure, *, heading_max_chars: int = 100, title_max_chars: int = 150
) -> Outline:
    blocks = [b for b in structure.blocks if not b.is_empty]

    markers: list[Marker] = []
    for index, block in enumerate(blocks):
        marker = parse_marker(block.text)
        if marker is None:
            continue
        marker.block_id, marker.index = block.id, index
        markers.append(marker)

    styled = [
        Heading(b.id, i, level, b.text.strip(),
                next((m.marker for m in markers if m.block_id == b.id), ""))
        for i, b in enumerate(blocks)
        if (level := _style_level(b)) is not None
    ]
    # Một style Heading duy nhất thường chỉ là cái tên đặt cho tài liệu, còn các
    # mục bên trong đánh số tay. Coi đó là "đã khai cấp mục" thì dàn ý chỉ có mỗi
    # dòng tên, mà mọi mục thật thì biến mất.
    explicit = len(styled) >= 2
    headings = styled if explicit else _marker_headings(markers, heading_max_chars)

    return Outline(
        blocks=blocks,
        headings=headings,
        markers=markers,
        title=_title(blocks, headings, title_max_chars),
        explicit=explicit,
    )
