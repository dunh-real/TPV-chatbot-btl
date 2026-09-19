"""Thẻ thông tin văn bản: gom phần thể thức thành một chunk riêng khi ingest.

Vì sao cần: reranker chấm CẢ chunk. Một công văn 900 token nói về kiểm kê, dòng
cuối ghi `| TỔNG GIÁM ĐỐC Nguyễn Văn Phúc |`, khi hỏi "Ai là Tổng giám đốc?" chỉ
được 0.07 điểm - cả đoạn đâu có nói về người ký, nó nói về kiểm kê. Chi tiết đúng
nằm trong chunk, nhưng chunk thì không *về* chi tiết đó.

Những thứ hay bị hỏi lẻ - ai ký, số mấy, ngày nào, gửi cho ai, về việc gì - đều là
SIÊU DỮ LIỆU của văn bản chứ không phải nội dung. Tách chúng thành một thẻ ngắn
thì thẻ đó *về* đúng những câu hỏi ấy, và reranker chấm cao một cách tự nhiên -
không cần nới ngưỡng, cũng không cần tới van cứu từ khoá.

Quốc hiệu và tiêu ngữ cố tình bỏ: văn bản nào cũng có, giống hệt nhau, đưa vào
chỉ tạo một chunk trùng lặp trong mọi tài liệu.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.documents.structure import _PATTERNS, build_chu_ky_pattern

logger = logging.getLogger(__name__)

CARD_SECTION = "Thông tin văn bản"

# Thành phần đưa vào thẻ, theo thứ tự đọc tự nhiên. Nhãn viết thành câu hoàn
# chỉnh để cả nhánh dense lẫn cross-encoder hiểu thẻ này nói về cái gì - ghi
# "chu_ky: Nguyễn Văn Phúc" thì chỉ BM25 dùng được.
LABELS: dict[str, str] = {
    "ten_loai": "Loại văn bản",
    "so_ky_hieu": "Số, ký hiệu văn bản",
    "dia_danh_ngay": "Địa danh và ngày ban hành",
    "trich_yeu": "Trích yếu nội dung",
    "kinh_gui": "Kính gửi",
    "noi_nhan": "Nơi nhận",
    "chu_ky": "Người ký",
}

# Dưới ngưỡng này thì tệp không phải văn bản hành chính: dựng thẻ cho một bản đặc
# tả kỹ thuật chỉ tạo rác trong kho.
MIN_FIELDS = 2

_MD_NOISE = re.compile(r"[*_`]+")
# "### BÁO CÁO" là heading Markdown; mẫu tên loại neo cả dòng nên vướng dấu #.
_MD_HEADING = re.compile(r"^#{1,6}\s*")
_TABLE_SEP = re.compile(r"^\|?[\s:|-]*-[\s:|-]*\|?$")
_INTRA_CELL = re.compile(r"\s{2,}")
_TRICH_YEU_VE = re.compile(r"^(?:Về|V/v)\b\s*:?\s*(.+)", re.I)


@dataclass(slots=True)
class DocumentCard:
    fields: dict[str, str] = field(default_factory=dict)
    text: str = ""

    @property
    def found(self) -> list[str]:
        return list(self.fields)


def _strip_md(value: str) -> str:
    """Bỏ ký tự nhấn mạnh và dấu heading của Markdown. GIỮ dấu hai chấm và gạch
    đầu dòng - cái trước để nhận nhãn "Nơi nhận:", cái sau để gom danh sách."""
    return " ".join(_MD_NOISE.sub(" ", _MD_HEADING.sub("", value.strip())).split())


def _tidy(value: str) -> str:
    return _strip_md(value).strip(" -–:;,.")


def logical_lines(text: str) -> list[str]:
    """Markdown -> danh sách dòng mà regex thể thức dùng được.

    MarkItDown gói phần đầu và phần ký của văn bản vào bảng hai cột, rồi nối các
    dòng trong một ô bằng khoảng trắng kép. Mọi mẫu neo đầu dòng (`^Số:`,
    `^Nơi nhận:`, dòng chức danh người ký) vì thế trượt sạch nếu quét thẳng.
    Ở đây nổ từng hàng bảng ra thành ô, rồi tách ô theo khoảng trắng kép.
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _TABLE_SEP.match(line):
            continue
        cells = line.split("|") if line.startswith("|") else [line]
        for cell in cells:
            for piece in _INTRA_CELL.split(cell.strip()):
                cleaned = _strip_md(piece)
                if cleaned:
                    out.append(cleaned)
    return out


def _chu_ky_pattern() -> re.Pattern[str]:
    """Danh sách chức danh lấy từ bộ tiêu chí để YAML sửa một chỗ, có hiệu lực
    cả khi soát lẫn khi ingest."""
    try:
        from app.documents.rules import get_rule_engine

        return build_chu_ky_pattern(get_rule_engine().chu_ky_titles)
    except Exception as exc:  # noqa: BLE001 - thiếu file tiêu chí thì dùng mặc định
        logger.debug("Dùng danh sách chức danh mặc định: %s", exc)
        return _PATTERNS["chu_ky"][0]


def _first_match(lines: list[str], pattern: re.Pattern[str]) -> tuple[int, re.Match[str]] | None:
    for i, line in enumerate(lines):
        match = pattern.search(line)
        if match is not None:
            return i, match
    return None


def _join_wrapped(lines: list[str], start: int, first: str, limit: int = 3) -> str:
    """Nối các dòng bị ngắt giữa chừng của một mệnh đề.

    Trích yếu dài bị Word xuống dòng thành hai đoạn; lấy mỗi dòng đầu thì câu cụt
    ngay giữa ("...tổng hợp tình hình nhân sự" mất hẳn "và trang thiết bị").
    """
    parts = [first]
    for line in lines[start : start + limit]:
        head = line[:1]
        if not head or head.isupper() or head in "-–+" or ":" in line[:24]:
            break
        parts.append(line)
    return " ".join(parts)


def _collect_list(lines: list[str], start: int, limit: int = 8) -> str:
    """Gom các gạch đầu dòng ngay sau một nhãn - "Nơi nhận" là một danh sách."""
    items = []
    for line in lines[start : start + limit]:
        if not line.startswith(("-", "–", "+")):
            break
        items.append(_tidy(line))
    return "; ".join(i for i in items if i)


def build_card(text: str, doc_title: str = "") -> DocumentCard | None:
    """Dựng thẻ từ Markdown của văn bản; None nếu không đủ dấu hiệu thể thức."""
    lines = logical_lines(text)
    if not lines:
        return None

    fields: dict[str, str] = {}
    patterns = {**_PATTERNS, "chu_ky": (_chu_ky_pattern(), 1)}

    for component_id in LABELS:
        pattern, group = patterns.get(component_id, (None, None))
        if pattern is None:
            continue
        hit = _first_match(lines, pattern)
        if hit is None:
            continue
        index, match = hit

        if component_id == "dia_danh_ngay":
            # Giữ cả dòng: "Hà Nội, ngày 18 tháng 9 năm 2026" đủ nghĩa hơn là
            # mỗi cụm ngày tháng tách rời khỏi địa danh.
            value = _tidy(lines[index])
        elif component_id == "noi_nhan":
            # Nhãn "Nơi nhận:" nằm một dòng, danh sách nơi nhận ở các dòng sau.
            same_line = _tidy(lines[index].split(":", 1)[-1])
            raw = same_line or _collect_list(lines, index + 1)
            # Các mục dính nhau trong một ô bảng: "A; - B; - C" -> "A; B; C".
            value = "; ".join(_tidy(part) for part in re.split(r"[;\n]\s*-?|\s+-\s+", raw)
                              if _tidy(part))
        elif component_id == "chu_ky":
            # Chức danh một dòng, họ tên dòng kế - "ai ký" cần cả hai.
            chuc_danh = _tidy(match.group(0))
            ten = next((_tidy(l) for l in lines[index + 1 : index + 3]
                        if _tidy(l) and not re.fullmatch(r"[0-9\W_]+", _tidy(l))
                        and not _tidy(l).lower().startswith(("đã ký", "ký,", "ký và"))), "")
            value = f"{chuc_danh} {ten}".strip()
        elif group is None:
            value = _tidy(match.group(0))
        else:
            value = _tidy(match.group(group) if group else match.group(0))
            if component_id == "trich_yeu":
                value = _tidy(_join_wrapped(lines, index + 1, value))[:300]

        if value:
            fields[component_id] = value

    # Văn bản có tên loại đặt trích yếu ngay dưới, mở đầu bằng "Về ..." chứ không
    # dùng dạng "V/v" của công văn.
    if "trich_yeu" not in fields and "ten_loai" in fields:
        hit = _first_match(lines, patterns["ten_loai"][0])
        if hit is not None:
            for line in lines[hit[0] + 1 : hit[0] + 3]:
                m = _TRICH_YEU_VE.match(line)
                if m and len(m.group(1)) <= 300:
                    start = lines.index(line, hit[0] + 1) + 1
                    fields["trich_yeu"] = _tidy(_join_wrapped(lines, start, m.group(1)))[:300]
                    break

    if len(fields) < MIN_FIELDS:
        return None

    head = f"{doc_title}. Thông tin thể thức văn bản:" if doc_title else "Thông tin thể thức văn bản:"
    body = [f"- {LABELS[cid]}: {fields[cid]}" for cid in LABELS if cid in fields]
    return DocumentCard(fields=fields, text="\n".join([head, *body]))
