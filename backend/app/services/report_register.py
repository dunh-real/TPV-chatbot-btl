"""Đọc sổ báo cáo của ứng dụng; danh mục phòng ban chỉ dùng để ghép nơi gửi."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.erp_repository import ErpDonViRepository
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

    from app.db.ai_session import repository
    import json

    async with repository() as repo:
        table = repo.table("Tasks")
        rows = await repo.rows("Tasks", table.c.RelatedEntityType == "AI_DocumentDrafts",
                               table.c.Status == "Completed", owned=True)
    submitted = {}
    for row in sorted(rows, key=lambda row: row["Id"]):
        result = json.loads(row["ResultJson"] or "{}")
        code = result.get("ma_don_vi")
        if code not in units or result.get("params", {}).get("ky") != ky:
            continue
        submitted[code] = {"ma_van_ban": result.get("so_ky_hieu") or str(row["RelatedEntityId"]),
            "ngay_van_ban": result.get("ngay_van_ban"), "file_path": row["OutputFileUrl"] or "",
            "ky": ky, "trong_ky": True}

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
