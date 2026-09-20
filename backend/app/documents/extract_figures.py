"""Trích số liệu từ file báo cáo đơn vị đã gửi, để đối chiếu với CSDL.

Số liệu chuẩn luôn là CSDL. Việc đọc lại file báo cáo phục vụ một mục đích khác:
phát hiện đơn vị báo cáo lệch với số liệu kiểm kê - hoặc do chép nhầm, hoặc do
kiểm kê sau khi đã gửi báo cáo. Chênh lệch được nêu ra để người duyệt xử lý, chứ
hệ thống không tự chọn bên nào đúng.

Số thật của một báo cáo kiểm kê nằm trong BẢNG, không nằm trong câu văn. Dò bằng
regex trên văn xuôi chỉ bắt được con số tổng mà người viết nhắc lại - mà họ có
thể nhắc sai, hoặc không nhắc. Nên ở đây đọc thẳng bảng: từng dòng chủng loại,
từng cột tình trạng, và cả dòng "Tổng cộng" nếu có. Tất định hoàn toàn, không
hỏi LLM - mọi con số đều chỉ lại được ô nào trong bảng nào.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mỗi chỉ tiêu có vài cách viết thường gặp trong văn bản hành chính.
FIELD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "nhan_su": [
        re.compile(r"[Nn]hân\s*sự[^0-9\n]{0,30}?(\d{1,6})"),
        re.compile(r"tổng\s*nhân\s*sự\s*(?:là|:)?\s*(\d{1,6})", re.I),
    ],
    "co_mat": [re.compile(r"có\s*mặt[^0-9\n]{0,20}?(\d{1,6})", re.I)],
    "vang": [re.compile(r"vắng[^0-9\n]{0,20}?(\d{1,6})", re.I)],
    "tong_so_thiet_bi": [
        # "trang" để tuỳ chọn để đọc được cả "80 đầu thiết bị" lẫn "80 đầu trang
        # thiết bị", nhưng "thiết bị" thì bắt buộc: bỏ nó ra, mẫu sẽ khớp trúng
        # chữ "bị" trong "bị hỏng", "bị mất" và đếm bừa.
        re.compile(r"(\d{1,6})\s*(?:đầu|loại)?\s*(?:trang\s*)?thiết\s*bị", re.I),
        re.compile(r"tổng\s*(?:số\s*)?(?:trang\s*)?thiết\s*bị[^0-9\n]{0,20}?(\d{1,6})", re.I),
    ],
}


# --------------------------------------------------------------------------- #
# Bảng kiểm kê trong báo cáo
# --------------------------------------------------------------------------- #
# Mỗi cơ quan đặt tên cột một kiểu; khớp theo cụm con, không đòi trùng khít.
INVENTORY_COLUMNS: dict[str, tuple[str, ...]] = {
    "ten": ("danh mục", "tên trang", "tên thiết bị", "loại trang", "chủng loại",
            "danh mục trang thiết bị", "tên tài sản"),
    "dvt": ("đvt", "đơn vị tính"),
    # Cột chữ, không phải cột đếm: mỗi dòng ghi tình trạng của chủng loại đó.
    # Nhận nó để bảng kiểu "Tên | Số lượng | Tình trạng" cũng đọc được, và để
    # bước vẽ biểu đồ có cái mà nhóm.
    "tinh_trang": ("tình trạng", "trạng thái", "hiện trạng"),
    "tong": ("tổng sl", "tổng số", "số lượng", "tổng cộng", "sl"),
    "tot": ("hoạt động tốt", "tình trạng tốt", "còn tốt", "sử dụng tốt", "tốt"),
    # KHÔNG để "bảo dưỡng" trần: cột "Bảo dưỡng gần nhất" là NGÀY, không phải số
    # lượng, mà khớp trúng thì cả cột ngày bị đếm thành thiết bị hỏng.
    "can_xu_ly": ("cần bảo dưỡng", "sửa chữa", "hư hỏng", "cần xử lý", "cần sửa"),
    "thanh_ly": ("đề xuất thanh lý", "thanh lý", "loại bỏ", "hỏng không sửa"),
}
# Thiếu cột "tổng số lượng" thì không phải bảng kiểm kê, chỉ là bảng nào đó.
_TEXT_COLUMNS = ("ten", "dvt", "tinh_trang")
_REQUIRED_COLUMNS = ("ten", "tong")
_MIN_MATCHED_COLUMNS = 3

_PIPE_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_PIPE_DIVIDER = re.compile(r"^[\s:|-]*-[\s:|-]*$")
_TOTAL_LABEL = re.compile(r"^\s*(?:tổng\s*cộng|cộng|tổng)\s*$", re.I)
_NUMBER = re.compile(r"^[\d.,\s]+$")


def markdown_tables(text: str) -> list[list[list[str]]]:
    """Tách các bảng pipe của Markdown thành lưới ô."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        match = _PIPE_ROW.match(line)
        if match is None:
            if current:
                tables.append(current)
                current = []
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if all(_PIPE_DIVIDER.match(c) or not c for c in cells) and any(cells):
            continue                      # dòng phân cách của Markdown
        current.append(cells)
    if current:
        tables.append(current)
    return tables


def parse_int(value: str) -> int | None:
    """"02" -> 2, "1.234" -> 1234, "" hoặc chữ -> None."""
    text = (value or "").strip()
    if not text or not _NUMBER.match(text):
        return None
    digits = re.sub(r"[.,\s]", "", text)
    return int(digits) if digits.isdigit() else None


def _map_columns(header: list[str]) -> dict[str, int]:
    """Nhãn cột -> chỉ số cột. Cột dài khớp trước để "tổng sl" không thua "sl"."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        label = " ".join(cell.lower().split())
        if not label:
            continue
        best: tuple[int, str] | None = None
        for field_name, aliases in INVENTORY_COLUMNS.items():
            if field_name in mapping:
                continue
            for alias in aliases:
                if alias in label and (best is None or len(alias) > best[0]):
                    best = (len(alias), field_name)
        if best is not None:
            mapping[best[1]] = index
    return mapping


@dataclass(slots=True)
class InventoryTable:
    """Bảng kiểm kê đọc được từ báo cáo."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)          # dòng "Tổng cộng" ghi gì
    computed: dict[str, int] = field(default_factory=dict)        # cộng lại từ các dòng
    columns: list[str] = field(default_factory=list)

    @property
    def total_mismatch(self) -> dict[str, tuple[int, int]]:
        """Chỉ tiêu mà dòng tổng ghi khác tổng các dòng - lỗi của chính báo cáo."""
        return {k: (v, self.computed[k]) for k, v in self.totals.items()
                if k in self.computed and self.computed[k] != v}

    @property
    def figures(self) -> dict[str, int]:
        """Số dùng để đối chiếu: ưu tiên dòng tổng, thiếu thì lấy tổng cộng lại."""
        return {**self.computed, **self.totals}

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns, "rows": self.rows,
            "totals": self.totals, "computed": self.computed,
            "total_mismatch": {k: {"ghi": a, "cong_lai": b}
                               for k, (a, b) in self.total_mismatch.items()},
        }


def extract_inventory(text: str) -> InventoryTable | None:
    """Tìm bảng kiểm kê trang thiết bị trong Markdown và đọc từng dòng."""
    for grid in markdown_tables(text):
        for header_index, header in enumerate(grid[:3]):
            mapping = _map_columns(header)
            if not all(k in mapping for k in _REQUIRED_COLUMNS):
                continue
            if len(mapping) < _MIN_MATCHED_COLUMNS:
                continue

            rows: list[dict[str, Any]] = []
            totals: dict[str, int] = {}
            numeric = [k for k in mapping if k not in _TEXT_COLUMNS]

            for raw in grid[header_index + 1:]:
                if len(raw) <= mapping["ten"]:
                    continue
                name = raw[mapping["ten"]].strip()
                values = {k: parse_int(raw[i]) for k, i in mapping.items()
                          if k in numeric and i < len(raw)}
                if not any(v is not None for v in values.values()):
                    continue
                if _TOTAL_LABEL.match(name):
                    totals = {k: v for k, v in values.items() if v is not None}
                    continue
                text_values = {k: raw[mapping[k]].strip()
                               for k in _TEXT_COLUMNS
                               if k != "ten" and k in mapping and mapping[k] < len(raw)}
                rows.append({"ten": name, **text_values,
                             **{k: v for k, v in values.items() if v is not None}})

            if not rows:
                continue
            # Cột map trúng nhưng không dòng nào ra số (cột ngày, cột ghi chú):
            # báo 0 ở đây là bịa ra một chỉ tiêu không hề có trong bảng.
            numeric = [k for k in numeric if any(isinstance(r.get(k), int) for r in rows)]
            rows = [{k: v for k, v in r.items() if k in _TEXT_COLUMNS or k in numeric}
                    for r in rows]
            totals = {k: v for k, v in totals.items() if k in numeric}
            computed = {k: sum(r[k] for r in rows if isinstance(r.get(k), int))
                        for k in numeric}
            return InventoryTable(rows=rows, totals=totals, computed=computed,
                                  columns=[c for c in header if c.strip()])
    return None


@dataclass(slots=True)
class Discrepancy:
    field: str
    label: str
    db_value: Any
    file_value: Any
    ma_don_vi: str = ""

    @property
    def message(self) -> str:
        return (f"{self.label}: báo cáo ghi {self.file_value}, "
                f"số liệu kiểm kê là {self.db_value}")


@dataclass(slots=True)
class ReconcileResult:
    ma_don_vi: str
    file_path: str = ""
    extracted: dict[str, int] = field(default_factory=dict)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    status: str = "matched"        # matched | mismatched | unreadable | no_file

    def as_dict(self) -> dict[str, Any]:
        return {
            "ma_don_vi": self.ma_don_vi, "file_path": self.file_path,
            "status": self.status, "extracted": self.extracted,
            "discrepancies": [
                {"field": d.field, "label": d.label, "db_value": d.db_value,
                 "file_value": d.file_value, "message": d.message}
                for d in self.discrepancies
            ],
        }


FIELD_LABELS = {
    "nhan_su": "Nhân sự", "co_mat": "Có mặt", "vang": "Vắng",
    "tong_so_thiet_bi": "Tổng thiết bị",
}


# Từ chỉ thời gian nằm giữa tên chỉ tiêu và con số -> con số đó là MỐC THỜI GIAN.
#
# Trích yếu "V/v báo cáo nhân sự và trang thiết bị tháng 8/2026" khớp mẫu nhân sự
# và trả về 8. Báo cáo của Phòng Kế toán ghi nhân sự 3, kiểm kê cũng 3, nhưng bản
# tổng hợp vẫn in ra một mục "ĐỐI CHIẾU SỐ LIỆU: báo cáo ghi 8, kiểm kê 3" - một
# chênh lệch không có thật, nằm trong văn bản trình ký.
_TIME_WORD_RE = re.compile(r"tháng|quý|năm|ngày|tuần|kỳ", re.I)
# "8/2026" - số nằm trong một mốc ngày tháng, không phải số liệu.
_PART_OF_DATE_RE = re.compile(r"\s*[/-]\s*\d")


def _dang_tin(match: re.Match[str], text: str) -> bool:
    """Con số bắt được có thật sự là số liệu, hay chỉ là mốc thời gian đi ngang qua."""
    truoc_so = match.group(0)[: match.start(1) - match.start(0)]
    if _TIME_WORD_RE.search(truoc_so):
        return False
    return not _PART_OF_DATE_RE.match(text[match.end(1):match.end(1) + 6])


def extract_figures(text: str) -> dict[str, int]:
    """Lấy các chỉ tiêu nhận ra được; chỉ tiêu không chắc chắn thì bỏ qua."""
    figures: dict[str, int] = {}
    for field_name, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                if not _dang_tin(match, text):
                    continue
                try:
                    figures[field_name] = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                break
            if field_name in figures:
                break
    return figures


def reconcile_file(
    ma_don_vi: str, file_path: str | Path | None, db_values: dict[str, Any]
) -> ReconcileResult:
    """So số trong file báo cáo với số trong CSDL."""
    if not file_path:
        return ReconcileResult(ma_don_vi=ma_don_vi, status="no_file")

    path = Path(file_path)
    if not path.exists():
        logger.info("Không thấy file báo cáo của %s: %s", ma_don_vi, path)
        return ReconcileResult(ma_don_vi=ma_don_vi, file_path=str(path), status="no_file")

    try:
        from app.documents.parser import parse_document

        text = parse_document(path).text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được báo cáo %s: %s", path.name, exc)
        return ReconcileResult(ma_don_vi=ma_don_vi, file_path=str(path), status="unreadable")

    extracted = extract_figures(text)
    discrepancies = [
        Discrepancy(field=field_name, label=FIELD_LABELS.get(field_name, field_name),
                    db_value=db_values[field_name], file_value=value, ma_don_vi=ma_don_vi)
        for field_name, value in extracted.items()
        if field_name in db_values and db_values[field_name] != value
    ]

    return ReconcileResult(
        ma_don_vi=ma_don_vi, file_path=str(path), extracted=extracted,
        discrepancies=discrepancies,
        status="mismatched" if discrepancies else "matched",
    )
