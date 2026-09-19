"""Lấy số liệu từ chính các báo cáo đơn vị đã gửi, thay vì từ CSDL.

CSDL kiểm kê là nguồn chuẩn khi nó có thật. Nhưng nhiều nơi chưa có CSDL - thứ
duy nhất tồn tại là tập báo cáo các đơn vị gửi lên, và số liệu nằm trong BẢNG của
những báo cáo đó. Module này đọc thẳng các bảng ấy rồi cộng lại.

Nguyên tắc giữ nguyên như nhánh CSDL: **không một con số nào do model sinh ra.**
Bảng được đọc tất định (`extract_figures.extract_inventory`), mỗi con số trong
kết quả đều chỉ ngược lại được về file nào, dòng nào của bảng nào. Chỉ tiêu nào
đọc được từ bảng thì đánh dấu `nguon="bang"`; chỉ tiêu nào chỉ dò được trong câu
văn thì `nguon="van_xuoi"` - kém chắc chắn hơn, và phải nói rõ ra.

Cái mà tài liệu KHÔNG cho được là so sánh kỳ trước: một báo cáo chỉ là ảnh chụp
một thời điểm. Vì vậy `prev`/`delta` luôn rỗng ở nhánh này, trừ khi có sẵn báo
cáo của kỳ trước để đọc cùng.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.documents.extract_figures import InventoryTable, extract_figures, extract_inventory

logger = logging.getLogger(__name__)

# Chỉ tiêu trong bảng kiểm kê -> khoá trong kết quả tổng hợp, để trùng tên với
# nhánh CSDL: phần dựng biểu đồ và viết nhận xét không cần biết số đến từ đâu.
_METRIC_KEYS = {
    "tong": "total_equipment",
    "tot": "good",
    "can_xu_ly": "needs_attention",
}


@dataclass(slots=True)
class UnitReport:
    """Một báo cáo đơn vị đã đọc xong."""

    file_path: str
    ma_don_vi: str = ""
    ten_don_vi: str = ""
    so_ky_hieu: str = ""
    ngay_van_ban: str = ""
    inventory: InventoryTable | None = None
    prose_figures: dict[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and (self.inventory is not None or bool(self.prose_figures))

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "ma_don_vi": self.ma_don_vi,
            "ten_don_vi": self.ten_don_vi,
            "so_ky_hieu": self.so_ky_hieu,
            "ngay_van_ban": self.ngay_van_ban,
            "inventory": self.inventory.as_dict() if self.inventory else None,
            "prose_figures": self.prose_figures,
            "error": self.error,
        }


def read_unit_report(path: str | Path, ma_don_vi: str = "", ten_don_vi: str = "") -> UnitReport:
    """Đọc một file báo cáo: thẻ thể thức + bảng kiểm kê + số trong câu văn."""
    file_path = Path(path)
    report = UnitReport(file_path=str(file_path), ma_don_vi=ma_don_vi, ten_don_vi=ten_don_vi)
    if not file_path.is_file():
        report.error = "không tìm thấy file"
        return report

    try:
        from app.rag.converter import get_converter

        markdown = get_converter().convert(file_path).text
    except Exception as exc:  # noqa: BLE001 - một file hỏng không làm chết cả mẻ
        logger.warning("Không đọc được báo cáo %s: %s", file_path.name, exc)
        report.error = f"không đọc được: {exc}"
        return report

    from app.rag.doc_card import build_card

    if (card := build_card(markdown, file_path.stem)) is not None:
        report.so_ky_hieu = card.fields.get("so_ky_hieu", "")
        report.ngay_van_ban = card.fields.get("dia_danh_ngay", "")
    # KHÔNG lấy "Kính gửi" làm tên đơn vị: đó là nơi NHẬN. Không biết đơn vị gửi
    # thì ghi tên tệp - sai tên đơn vị trong báo cáo tổng hợp còn tệ hơn là không
    # ghi tên. Gọi từ sổ văn bản thì đã có mã và tên đơn vị truyền vào sẵn.
    if not report.ten_don_vi:
        report.ten_don_vi = file_path.stem

    report.inventory = extract_inventory(markdown)
    report.prose_figures = extract_figures(markdown)
    if report.inventory is None and not report.prose_figures:
        report.error = "không tìm thấy bảng kiểm kê lẫn số liệu trong văn bản"
    return report


def _metric(value: int, total: int | None = None) -> dict[str, Any]:
    """Cùng hình dạng với Metric của nhánh CSDL, nhưng không có kỳ trước."""
    return {
        "value": value,
        "prev": None,
        "delta": None,
        "delta_pct": None,
        "share_pct": round(value / total * 100, 1) if total else None,
    }


def aggregate_reports(reports: list[UnitReport], period: str) -> dict[str, Any]:
    """Cộng số liệu trang thiết bị từ nhiều báo cáo thành một kết quả tổng hợp.

    Trả về đúng hình dạng mà `get_equipment_statistics` trả về, để bước vẽ biểu
    đồ, kiểm chứng số và viết nhận xét dùng lại được nguyên vẹn.
    """
    usable = [r for r in reports if r.ok]
    breakdown: list[dict[str, Any]] = []
    totals = {"tong": 0, "tot": 0, "can_xu_ly": 0, "thanh_ly": 0}
    types = 0
    sources: list[dict[str, Any]] = []
    notes: list[str] = []

    for report in usable:
        if report.inventory is None:
            notes.append(f"{report.ten_don_vi or Path(report.file_path).name}: không có bảng "
                         "kiểm kê, chỉ dò được số trong câu văn - độ tin cậy thấp hơn.")
            continue
        figures = report.inventory.figures
        types += len(report.inventory.rows)
        for key in totals:
            totals[key] += int(figures.get(key, 0) or 0)

        for row in report.inventory.rows:
            breakdown.append({
                "ma_don_vi": report.ma_don_vi,
                "ten_don_vi": report.ten_don_vi or Path(report.file_path).stem,
                "ten_trang_bi": row.get("ten", ""),
                "so_luong": row.get("tong", 0),
                "tinh_trang": row.get("tinh_trang", ""),
                "hoat_dong_tot": row.get("tot"),
                "can_xu_ly": row.get("can_xu_ly"),
                "nguon_file": Path(report.file_path).name,
            })

        sources.append({
            "ma_don_vi": report.ma_don_vi,
            "ten_don_vi": report.ten_don_vi,
            "file": Path(report.file_path).name,
            "so_ky_hieu": report.so_ky_hieu,
            "so_dong_bang": len(report.inventory.rows),
            "figures": figures,
        })
        # Báo cáo tự mâu thuẫn: dòng "Tổng cộng" khác tổng các dòng trên nó.
        for key, (ghi, cong_lai) in report.inventory.total_mismatch.items():
            notes.append(f"{report.ten_don_vi or Path(report.file_path).name}: dòng Tổng cộng "
                         f"ghi {ghi} nhưng cộng các dòng ra {cong_lai} (chỉ tiêu {key}).")

    failed = [{"file": Path(r.file_path).name, "ly_do": r.error} for r in reports if not r.ok]
    metrics = {
        _METRIC_KEYS["tong"]: _metric(totals["tong"]),
        _METRIC_KEYS["tot"]: _metric(totals["tot"], totals["tong"]),
        _METRIC_KEYS["can_xu_ly"]: _metric(totals["can_xu_ly"], totals["tong"]),
        "equipment_types": _metric(types),
    }

    # Ràng buộc nghiệp vụ kiểm ngay tại đây, không để model làm mượt: tốt + cần
    # xử lý phải bằng tổng, lệch thì nêu thành một mục riêng trong báo cáo.
    consistency = []
    if totals["tong"] and totals["tot"] + totals["can_xu_ly"] != totals["tong"]:
        consistency.append({
            "ma_don_vi": "",
            "message": f"Tổng trang bị {totals['tong']} nhưng tốt {totals['tot']} + cần xử lý "
                       f"{totals['can_xu_ly']} = {totals['tot'] + totals['can_xu_ly']}.",
        })

    return {
        "period": period,
        "compare_to": None,
        "scope": {
            "units_requested": len(reports),
            "units_with_data": len(sources),
            "units_missing": [f["file"] for f in failed],
            "nguon": "tai_lieu",
        },
        "metrics": metrics,
        "breakdown": breakdown,
        "consistency": consistency,
        "sources": sources,
        "failed": failed,
        "notes": notes,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }
