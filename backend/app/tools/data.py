"""Công cụ truy vấn số liệu nghiệp vụ cho báo cáo tổng hợp.

Đây là tầng DUY NHẤT chạm vào số liệu. LLM không sinh SQL, không nhận quyền truy
vấn, và không phải tính bất cứ phép nào: mọi con số sẽ xuất hiện trong câu văn -
kể cả tỷ lệ phần trăm và mức tăng giảm - đều được tính sẵn ở đây.

Nguyên tắc kiểm tra: nếu một con số trong báo cáo không có mặt trong kết quả của
các tool này thì nó là số bịa.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DonVi, KiemKeTrangBi, KyKiemKe, VanBan
from app.tools.base import ToolError, ToolSpec

logger = logging.getLogger(__name__)

KY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TINH_TRANG_TOT = {"tốt", "tot", "good"}

__all__ = [
    "AggregateResult", "ConsistencyIssue", "Metric", "ToolError", "ToolSpec", "TOOLS",
    "call_tool", "describe_tools", "get_equipment_statistics", "get_personnel_statistics",
    "get_reporting_status", "previous_ky", "validate_ky",
]


@dataclass(slots=True)
class Metric:
    """Một chỉ tiêu kèm sẵn mọi con số sẽ được nhắc tới trong văn bản."""

    value: int | float
    prev: int | float | None = None
    delta: int | float | None = None
    delta_pct: float | None = None
    share_pct: float | None = None

    @classmethod
    def build(cls, value, prev=None, total=None) -> "Metric":
        delta = None if prev is None else round(value - prev, 2)
        delta_pct = None
        if prev:
            delta_pct = round((value - prev) / prev * 100, 1)
        share_pct = round(value / total * 100, 1) if total else None
        return cls(value=value, prev=prev, delta=delta, delta_pct=delta_pct,
                   share_pct=share_pct)


@dataclass(slots=True)
class ConsistencyIssue:
    ma_don_vi: str
    message: str


@dataclass(slots=True)
class AggregateResult:
    period: str
    compare_to: str | None
    scope: dict[str, Any]
    metrics: dict[str, Metric] = field(default_factory=dict)
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    consistency: list[ConsistencyIssue] = field(default_factory=list)
    computed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "compare_to": self.compare_to,
            "scope": self.scope,
            "metrics": {k: asdict(v) for k, v in self.metrics.items()},
            "breakdown": self.breakdown,
            "consistency": [asdict(c) for c in self.consistency],
            "computed_at": self.computed_at or datetime.now().isoformat(timespec="seconds"),
        }

    @property
    def is_consistent(self) -> bool:
        return not self.consistency


# --------------------------------------------------------------------------- #
# Tiện ích kỳ
# --------------------------------------------------------------------------- #
def validate_ky(ky: str) -> str:
    if not isinstance(ky, str) or not KY_RE.match(ky):
        raise ToolError(f'Kỳ phải có dạng "YYYY-MM", nhận được {ky!r}')
    return ky


def previous_ky(ky: str) -> str:
    year, month = int(ky[:4]), int(ky[5:])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


async def _valid_units(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(select(DonVi.ma_don_vi, DonVi.ten_don_vi))
    return {ma: ten for ma, ten in result}


async def _resolve_units(session: AsyncSession, ma_don_vi: list[str] | str | None) -> list[str]:
    units = await _valid_units(session)
    if not ma_don_vi:
        return sorted(units)
    requested = [ma_don_vi] if isinstance(ma_don_vi, str) else list(ma_don_vi)
    unknown = [code for code in requested if code not in units]
    if unknown:
        raise ToolError(f"Đơn vị không tồn tại: {', '.join(unknown)}")
    return requested


# --------------------------------------------------------------------------- #
# Tool 1: thống kê quân số
# --------------------------------------------------------------------------- #
async def get_personnel_statistics(
    session: AsyncSession,
    ky: str,
    ma_don_vi: list[str] | str | None = None,
    compare_to: str | None = None,
) -> AggregateResult:
    """Tổng hợp quân số của các đơn vị trong kỳ, kèm so sánh với kỳ trước."""
    ky = validate_ky(ky)
    compare_to = validate_ky(compare_to) if compare_to else previous_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)

    async def load(period: str) -> dict[str, KyKiemKe]:
        result = await session.execute(
            select(KyKiemKe).where(KyKiemKe.ky == period, KyKiemKe.ma_don_vi.in_(units))
        )
        return {row.ma_don_vi: row for row in result.scalars()}

    current, previous = await load(ky), await load(compare_to)

    def total(rows: dict[str, KyKiemKe], attr: str) -> int:
        return sum(getattr(row, attr) for row in rows.values())

    quan_so = total(current, "quan_so")
    metrics = {
        "total_personnel": Metric.build(quan_so, total(previous, "quan_so") or None),
        "present": Metric.build(total(current, "co_mat"),
                                total(previous, "co_mat") or None, total=quan_so),
        "absent": Metric.build(total(current, "vang"),
                               total(previous, "vang") or None, total=quan_so),
        "training": Metric.build(total(current, "di_hoc"),
                                 total(previous, "di_hoc") or None, total=quan_so),
        "leave": Metric.build(total(current, "nghi_phep"),
                              total(previous, "nghi_phep") or None, total=quan_so),
    }

    # Ràng buộc nghiệp vụ phải được kiểm ở đây, trước khi số liệu tới tay LLM.
    issues: list[ConsistencyIssue] = []
    for code, row in current.items():
        if row.co_mat + row.vang != row.quan_so:
            issues.append(ConsistencyIssue(
                code, f"{names.get(code, code)}: có mặt {row.co_mat} + vắng {row.vang} "
                      f"≠ quân số {row.quan_so}"))
        if row.di_hoc + row.nghi_phep > row.vang:
            issues.append(ConsistencyIssue(
                code, f"{names.get(code, code)}: đi học {row.di_hoc} + nghỉ phép "
                      f"{row.nghi_phep} > số vắng {row.vang}"))

    breakdown = [
        {"ma_don_vi": code, "ten_don_vi": names.get(code, code),
         "quan_so": row.quan_so, "co_mat": row.co_mat, "vang": row.vang,
         "di_hoc": row.di_hoc, "nghi_phep": row.nghi_phep,
         "ngay_kiem_ke": row.ngay_kiem_ke.isoformat() if row.ngay_kiem_ke else None}
        for code, row in sorted(current.items())
    ]

    return AggregateResult(
        period=ky, compare_to=compare_to if previous else None,
        scope={"units_requested": len(units), "units_with_data": len(current),
               "units_missing": sorted(set(units) - set(current))},
        metrics=metrics, breakdown=breakdown, consistency=issues,
    )


# --------------------------------------------------------------------------- #
# Tool 2: thống kê trang thiết bị
# --------------------------------------------------------------------------- #
async def get_equipment_statistics(
    session: AsyncSession,
    ky: str,
    ma_don_vi: list[str] | str | None = None,
    compare_to: str | None = None,
) -> AggregateResult:
    ky = validate_ky(ky)
    compare_to = validate_ky(compare_to) if compare_to else previous_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)

    async def load(period: str) -> list[tuple[str, KiemKeTrangBi]]:
        result = await session.execute(
            select(KyKiemKe.ma_don_vi, KiemKeTrangBi)
            .join(KiemKeTrangBi, KiemKeTrangBi.kiem_ke_id == KyKiemKe.id)
            .where(KyKiemKe.ky == period, KyKiemKe.ma_don_vi.in_(units))
        )
        return [(row[0], row[1]) for row in result]

    current, previous = await load(ky), await load(compare_to)

    def summarize(rows) -> tuple[int, int, int]:
        tong = sum(tb.so_luong for _, tb in rows)
        tot = sum(tb.so_luong for _, tb in rows if tb.tinh_trang.lower() in TINH_TRANG_TOT)
        return tong, tot, tong - tot

    tong, tot, can_xu_ly = summarize(current)
    prev_tong, prev_tot, prev_can = summarize(previous)

    metrics = {
        "total_equipment": Metric.build(tong, prev_tong or None),
        "good": Metric.build(tot, prev_tot or None, total=tong),
        "needs_attention": Metric.build(can_xu_ly, prev_can or None, total=tong),
        "equipment_types": Metric.build(len({tb.ten_trang_bi for _, tb in current})),
    }

    breakdown = [
        {"ma_don_vi": code, "ten_don_vi": names.get(code, code),
         "ten_trang_bi": tb.ten_trang_bi, "so_luong": tb.so_luong,
         "tinh_trang": tb.tinh_trang,
         "bao_duong_cuoi": tb.bao_duong_cuoi.isoformat() if tb.bao_duong_cuoi else None}
        for code, tb in sorted(current, key=lambda r: (r[0], r[1].ten_trang_bi))
    ]

    return AggregateResult(
        period=ky, compare_to=compare_to if previous else None,
        scope={"units_requested": len(units),
               "units_with_data": len({code for code, _ in current}),
               "units_missing": sorted(set(units) - {code for code, _ in current})},
        metrics=metrics, breakdown=breakdown,
    )


# --------------------------------------------------------------------------- #
# Tool 3: tình hình nộp báo cáo
# --------------------------------------------------------------------------- #
async def get_reporting_status(
    session: AsyncSession, ky: str, ma_don_vi: list[str] | str | None = None
) -> dict[str, Any]:
    """Đơn vị nào đã gửi báo cáo kỳ này, đơn vị nào chưa.

    Thông tin có giá trị nhất của một báo cáo tổng hợp thường không phải con số
    tổng, mà là "ai chưa nộp".
    """
    ky = validate_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)

    year, month = int(ky[:4]), int(ky[5:])
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    result = await session.execute(
        select(VanBan).where(VanBan.loai_van_ban == "bao_cao_di")
    )
    submitted: dict[str, dict[str, Any]] = {}
    for van_ban in result.scalars():
        # Khớp theo tên đơn vị gửi, ưu tiên báo cáo có kỳ nằm trong khoảng.
        for code in units:
            if names.get(code, "") and names[code] in (van_ban.noi_gui or ""):
                in_period = van_ban.ngay_van_ban is None or start <= van_ban.ngay_van_ban < end
                if code not in submitted or in_period:
                    submitted[code] = {
                        "ma_van_ban": van_ban.ma_van_ban,
                        "ngay_van_ban": van_ban.ngay_van_ban.isoformat()
                        if van_ban.ngay_van_ban else None,
                        "file_path": van_ban.file_path,
                        "trong_ky": in_period,
                    }

    reported = sorted(code for code, info in submitted.items() if info["trong_ky"])
    missing = sorted(set(units) - set(reported))
    return {
        "period": ky,
        "units_total": len(units),
        "units_reported": len(reported),
        "reported": [{"ma_don_vi": c, "ten_don_vi": names.get(c, c), **submitted[c]}
                     for c in reported],
        "missing": [{"ma_don_vi": c, "ten_don_vi": names.get(c, c)} for c in missing],
    }


# --------------------------------------------------------------------------- #
# Registry - LLM chỉ được chọn trong danh sách này
# --------------------------------------------------------------------------- #
# Bí danh theo khoảng thời gian: người dùng nói "từ tháng 6 đến tháng 9" chứ không
# nói "kỳ 2026-09, so sánh với 2026-06". Số liệu kiểm kê chốt theo tháng nên một
# khoảng quy về đúng hai mốc: kỳ báo cáo (cuối khoảng) và kỳ đối chiếu (đầu khoảng).
PERIOD_ALIASES = {"end_date": "ky", "start_date": "compare_to", "unit": "ma_don_vi"}

TOOLS: dict[str, ToolSpec] = {
    "get_personnel_statistics": ToolSpec(
        name="get_personnel_statistics",
        description="Thống kê quân số (tổng, có mặt, vắng, đi học, nghỉ phép) theo kỳ, "
                    "kèm so sánh với kỳ trước",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "mã đơn vị hoặc danh sách mã; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)",
                    "compare_to": "kỳ để so sánh; bỏ trống = kỳ liền trước "
                                  "(bí danh: start_date)"},
        func=get_personnel_statistics,
        needs_session=True,
        aliases=PERIOD_ALIASES,
    ),
    "get_equipment_statistics": ToolSpec(
        name="get_equipment_statistics",
        description="Thống kê trang thiết bị (tổng số, tình trạng tốt, cần xử lý) theo kỳ",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "mã đơn vị hoặc danh sách mã; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)",
                    "compare_to": "kỳ để so sánh; bỏ trống = kỳ liền trước "
                                  "(bí danh: start_date)"},
        func=get_equipment_statistics,
        needs_session=True,
        aliases=PERIOD_ALIASES,
    ),
    "get_reporting_status": ToolSpec(
        name="get_reporting_status",
        description="Danh sách đơn vị đã gửi và chưa gửi báo cáo trong kỳ",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "giới hạn trong các đơn vị này; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)"},
        func=get_reporting_status,
        needs_session=True,
        aliases={"end_date": "ky", "unit": "ma_don_vi"},
    ),
}


def describe_tools() -> str:
    """Mô tả tool để đưa vào prompt chọn tool."""
    return "\n".join(spec.describe() for spec in TOOLS.values())


def resolve_aliases(spec: ToolSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Đổi bí danh về tên tham số thật; bí danh trùng với tên thật thì tên thật thắng."""
    resolved = {k: v for k, v in kwargs.items() if k not in spec.aliases}
    for alias, real in spec.aliases.items():
        if alias in kwargs and real not in kwargs:
            resolved[real] = kwargs[alias]
    return resolved


async def call_tool(session: AsyncSession, name: str, **kwargs) -> Any:
    """Gọi tool theo tên; tên lạ hoặc tham số sai đều bị chặn tại đây."""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Không có công cụ tên {name!r}. "
                        f"Chỉ dùng được: {', '.join(TOOLS)}")
    kwargs = resolve_aliases(spec, kwargs)
    allowed = set(spec.parameters)
    unknown = set(kwargs) - allowed
    if unknown:
        raise ToolError(f"{name}: tham số không hợp lệ {sorted(unknown)}")
    return await spec.func(session, **kwargs)
