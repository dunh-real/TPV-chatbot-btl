"""Đối chiếu số liệu trong văn bản LLM viết với dữ liệu gốc từ CSDL.

Đây là van chặn quan trọng nhất của workflow 3. Con số bịa trong báo cáo hành chính
nguy hiểm hơn câu văn vụng: nó trông hợp lý, không ai phát hiện khi đọc lướt, và
được dùng để ra quyết định.

Nguyên tắc: mọi số xuất hiện trong phần LLM viết phải truy về được dữ liệu gốc.
Trừ hai ngoại lệ có thật trong văn phong hành chính:
    - số đi kèm đơn vị thời gian: "quá hạn 12 tháng", "trong 3 năm"
    - số thứ tự mục: "mục 2", "điểm 3"

GIỚI HẠN ĐÃ BIẾT: van này bắt số bịa "lạ" (70 người, 15.000.000 đồng) rất tốt,
nhưng không bắt được số nhỏ trùng ngẫu nhiên - model viết "tăng 5" trong khi thực
tế tăng 3 vẫn lọt nếu số 5 tình cờ xuất hiện ở chỗ khác trong cùng phạm vi dữ liệu.
Vì vậy phải thu hẹp phạm vi đối chiếu đúng bằng số liệu đã đưa cho model ở mục đó,
và văn bản vẫn cần người duyệt trước khi phát hành.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# "12 tháng", "6 năm", "30 ngày" - số chỉ khoảng thời gian, không phải số liệu
DURATION_RE = re.compile(r"\d+(?:[.,]\d+)*\s*(?:tháng|năm|ngày|tuần|quý|giờ)\b", re.I)
ORDINAL_RE = re.compile(r"(?:mục|điểm|khoản|điều|phần|bảng|biểu)\s+\d+", re.I)


@dataclass(slots=True)
class NumberCheck:
    section_id: str
    unverified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unverified


def _normalize_number(raw: str) -> str:
    """"5.000.000" và "5,000,000" và "5000000" là một."""
    return raw.replace(".", "").replace(",", "").lstrip("0") or "0"


def collect_known_numbers(data: Any, known: set[str] | None = None) -> set[str]:
    """Mọi số truy được từ dữ liệu gốc, kể cả số nằm trong chuỗi ngày tháng."""
    known = known if known is not None else set()

    if isinstance(data, dict):
        for value in data.values():
            collect_known_numbers(value, known)
    elif isinstance(data, (list, tuple)):
        for item in data:
            collect_known_numbers(item, known)
    elif isinstance(data, bool):
        pass
    elif isinstance(data, (int, float)):
        known.add(_normalize_number(str(data)))
        # Số thực tròn: Python viết 75.0, người viết văn bản viết "75%".
        # `_normalize_number` coi dấu chấm là dấu phân cách nghìn (đúng với
        # "5.000.000"), nên 75.0 thành "750" và câu "tăng 75%" bị kết luận là số
        # bịa - báo cáo đúng bị chặn không xuất file. Nhận cả hai dạng.
        if isinstance(data, float) and data.is_integer():
            known.add(_normalize_number(str(int(data))))
    elif isinstance(data, str):
        for match in NUMBER_RE.findall(data):
            known.add(_normalize_number(match))
    return known


def check_numbers(text: str, known: set[str], section_id: str = "") -> NumberCheck:
    """Tìm những con số trong `text` không truy được về dữ liệu gốc."""
    masked = ORDINAL_RE.sub(" ", DURATION_RE.sub(" ", text))

    unverified: list[str] = []
    for raw in NUMBER_RE.findall(masked):
        normalized = _normalize_number(raw)
        if normalized in known:
            continue
        # Số liệu ghép từ nhiều phần (ví dụ "2026" trong "tháng 8/2026") vẫn tính là
        # hợp lệ nếu từng phần đều truy được.
        if all(_normalize_number(part) in known for part in re.split(r"[.,]", raw) if part):
            continue
        unverified.append(raw)

    return NumberCheck(section_id=section_id, unverified=unverified)
