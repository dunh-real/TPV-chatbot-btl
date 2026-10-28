"""Hai trường thể thức người dùng được nhập thay cho giá trị hệ thống tự sinh.

    so_ky_hieu     số văn bản ("15/BC-HCQT")  - để trống thì cấp số tiếp theo trong sổ văn bản
    ngay_ban_hanh  ngày tháng năm ban hành    - để trống thì lấy ngày lập (hôm nay)

Vị trí trên văn bản không đổi: dòng "Số: ..." và dòng "<địa danh>, ngày ... tháng ... năm ..." của phần
thể thức (dựng bằng code), hoặc ô {{so_ky_hieu}} / {{ngay_bao_cao}} của file .docx mẫu. Ngày ban hành cũng là
ngày ghi vào sổ văn bản.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

KHOA_SO = "so_ky_hieu"
KHOA_NGAY = "ngay_ban_hanh"

_DMY = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$")
_YMD = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def ngay_tieng_viet(value: date) -> str:
    return f"ngày {value.day:02d} tháng {value.month} năm {value.year}"


def doc_ngay_ban_hanh(inputs: dict[str, Any] | None, mac_dinh: date | None = None) -> date:
    """Ngày ban hành người dùng nhập ("05/10/2026", "5-10-2026", "2026-10-05"); để trống thì `mac_dinh`/hôm nay.

    Nhập sai thì báo lỗi chứ không lặng lẽ lấy hôm nay: ngày trên văn bản trình ký sai là sai pháp lý.
    """
    raw = str((inputs or {}).get(KHOA_NGAY) or "").strip()
    if not raw:
        return mac_dinh or date.today()
    if (m := _DMY.match(raw)):
        d, mo, y = (int(x) for x in m.groups())
    elif (m := _YMD.match(raw)):
        y, mo, d = (int(x) for x in m.groups())
    else:
        raise ValueError(f"Ngày ban hành \"{raw}\" không đúng định dạng ngày/tháng/năm (ví dụ 05/10/2026).")
    try:
        return date(y, mo, d)
    except ValueError as exc:
        raise ValueError(f"Ngày ban hành \"{raw}\" không có thật.") from exc


def ngay_ban_hanh_an_toan(inputs: dict[str, Any] | None) -> date:
    """Như `doc_ngay_ban_hanh` nhưng không ném lỗi - cho chỗ xem trước/điền lại sau khi đã kiểm ở bước xuất."""
    try:
        return doc_ngay_ban_hanh(inputs)
    except ValueError:
        return date.today()


def so_nguoi_dung(inputs: dict[str, Any] | None) -> str:
    return " ".join(str((inputs or {}).get(KHOA_SO) or "").split())


async def kiem_so_van_ban(so_ky_hieu: str) -> str:
    """Kiểm tra số đã đăng ký trên MSSQL; không đọc sổ SQLite cũ."""
    if not so_ky_hieu:
        return ""
    from app.db.ai_session import repository, current_task_id
    async with repository() as repo:
        exists = await repo.number_exists(so_ky_hieu, exclude_task=current_task_id.get())
    return f'Số văn bản "{so_ky_hieu}" đã có trong sổ MSSQL. Vui lòng dùng số khác.' if exists else ""
