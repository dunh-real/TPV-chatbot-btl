"""Đọc sổ báo cáo của ứng dụng; danh mục phòng ban chỉ dùng để ghép nơi gửi."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.erp_repository import ErpDonViRepository
from app.db.models import VanBan
from app.tools.base import ToolError

KY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

def validate_ky(ky: str) -> str:
    if not isinstance(ky, str) or not KY_RE.match(ky):
        raise ToolError(f'Kỳ phải có dạng "YYYY-MM", nhận được {ky!r}')
    return ky


def previous_ky(ky: str) -> str:
    year, month = int(ky[:4]), int(ky[5:])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


async def _valid_units(session: AsyncSession) -> dict[str, str]:
    return await ErpDonViRepository(session).name_map()


def _khoa_ten(text: str) -> str:
    """Khoá so tên đơn vị: bỏ dấu, bỏ hoa thường, bỏ khoảng trắng thừa."""
    phang = unicodedata.normalize("NFD", str(text))
    phang = "".join(c for c in phang if unicodedata.category(c) != "Mn")
    return " ".join(phang.replace("đ", "d").replace("Đ", "D").split()).lower()


async def _resolve_units(session: AsyncSession, ma_don_vi: list[str] | str | None) -> list[str]:
    """Đổi mã hoặc tên phòng ban thành mã hợp lệ trong tenant đang xét."""
    units = await _valid_units(session)
    if not ma_don_vi:
        return sorted(units)

    requested = [ma_don_vi] if isinstance(ma_don_vi, str) else list(ma_don_vi)
    theo_ten = {_khoa_ten(name): code for code, name in units.items()}

    resolved: list[str] = []
    unknown: list[str] = []
    for item in requested:
        key = str(item).strip()
        code = key if key in units else theo_ten.get(_khoa_ten(key))
        if code is None:
            unknown.append(key)
        elif code not in resolved:
            resolved.append(code)

    if unknown:
        danh_muc = "; ".join(f"{code} = {name}" for code, name in sorted(units.items()))
        raise ToolError(f"Đơn vị không tồn tại: {', '.join(unknown)}. "
                        f"Chỉ có các đơn vị sau: {danh_muc}")
    return resolved


def _don_vi_cua(van_ban: VanBan, units: list[str], names: dict[str, str]) -> list[str]:
    """Văn bản này là của đơn vị nào, trong phạm vi đang hỏi."""
    if van_ban.ma_don_vi:
        return [van_ban.ma_don_vi] if van_ban.ma_don_vi in units else []
    noi_gui = van_ban.noi_gui or ""
    return [code for code in units if names.get(code) and names[code] in noi_gui]


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

    # Sổ văn bản do chính hệ thống này ghi nên nằm ở CSDL app, không phải ERP.
    from app.db.session import session_scope

    async with session_scope() as app_session:
        result = await app_session.execute(
            select(VanBan).where(VanBan.loai_van_ban == "bao_cao_di")
        )
        van_ban_list = list(result.scalars())

    submitted: dict[str, dict[str, Any]] = {}
    for van_ban in van_ban_list:
        # Bản ghi mới có sẵn mã đơn vị và kỳ -> khớp thẳng, không phải đoán.
        #
        # Bản ghi cũ thì dò theo tên đơn vị trong `noi_gui` và lấy `ngay_van_ban`
        # làm mốc kỳ. Cách cũ giữ lại để sổ văn bản có sẵn không rỗng đi sau khi
        # đổi lược đồ, nhưng nó sai một cách có hệ thống: báo cáo kỳ tháng 8 ký
        # ngày 19/9 bị tính là ngoài kỳ, nên mục "tình hình gửi báo cáo" luôn ra
        # 0 đơn vị đã gửi.
        for code in _don_vi_cua(van_ban, units, names):
            if van_ban.ky:
                in_period = van_ban.ky == ky
            else:
                in_period = van_ban.ngay_van_ban is None or start <= van_ban.ngay_van_ban < end
            if code not in submitted or in_period:
                submitted[code] = {
                    "ma_van_ban": van_ban.ma_van_ban,
                    "ngay_van_ban": van_ban.ngay_van_ban.isoformat()
                    if van_ban.ngay_van_ban else None,
                    "file_path": van_ban.file_path,
                    "ky": van_ban.ky,
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
