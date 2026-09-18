"""Trích số liệu từ file báo cáo đơn vị đã gửi, để đối chiếu với CSDL.

Số liệu chuẩn luôn là CSDL. Việc đọc lại file báo cáo phục vụ một mục đích khác:
phát hiện đơn vị báo cáo lệch với số liệu kiểm kê - hoặc do chép nhầm, hoặc do
kiểm kê sau khi đã gửi báo cáo. Chênh lệch được nêu ra để người duyệt xử lý, chứ
hệ thống không tự chọn bên nào đúng.
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
    "quan_so": [
        re.compile(r"[Qq]uân\s*số[^0-9\n]{0,30}?(\d{1,6})"),
        re.compile(r"tổng\s*quân\s*số\s*(?:là|:)?\s*(\d{1,6})", re.I),
    ],
    "co_mat": [re.compile(r"có\s*mặt[^0-9\n]{0,20}?(\d{1,6})", re.I)],
    "vang": [re.compile(r"vắng[^0-9\n]{0,20}?(\d{1,6})", re.I)],
    "tong_so_trang_bi": [
        re.compile(r"(\d{1,6})\s*(?:đầu|loại)?\s*trang\s*(?:thiết\s*)?bị", re.I),
        re.compile(r"tổng\s*(?:số\s*)?trang\s*(?:thiết\s*)?bị[^0-9\n]{0,20}?(\d{1,6})", re.I),
    ],
}


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
    "quan_so": "Quân số", "co_mat": "Có mặt", "vang": "Vắng",
    "tong_so_trang_bi": "Tổng trang thiết bị",
}


def extract_figures(text: str) -> dict[str, int]:
    """Lấy các chỉ tiêu nhận ra được; chỉ tiêu không chắc chắn thì bỏ qua."""
    figures: dict[str, int] = {}
    for field_name, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                try:
                    figures[field_name] = int(match.group(1))
                except (TypeError, ValueError):
                    continue
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
