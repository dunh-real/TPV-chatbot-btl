"""Đọc danh mục phòng ban cho định tuyến tài liệu; số liệu nghiệp vụ dùng discovery."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_tenant_id
from app.db.erp_models import WorkDepartment
from app.tools.base import ToolError


class ErpDonViRepository:
    """Phòng ban ERP đóng vai "đơn vị" của các báo cáo."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # Cache trong phiên và theo tenant, tránh lặp truy vấn danh mục.
    _CACHE_KEY = "erp_don_vi_hien_tai"

    async def list_all(self, moment: datetime | None = None) -> list[WorkDepartment]:
        cache = self.session.info.setdefault(self._CACHE_KEY, {})
        tenant_id = current_tenant_id()
        if moment is None and tenant_id in cache:
            return cache[tenant_id]

        if type(tenant_id) is not int:
            raise ToolError("Danh mục phòng ban yêu cầu tenant cụ thể")
        cutoff = moment or datetime.now()
        stmt = select(WorkDepartment).where(
            WorkDepartment.tenant_id == tenant_id,
            WorkDepartment.creation_time < cutoff,
            (WorkDepartment.is_deleted == False) | (WorkDepartment.deletion_time >= cutoff),
        ).order_by(WorkDepartment.code)
        rows = list((await self.session.execute(stmt)).scalars())
        if moment is None:
            cache[tenant_id] = rows
        return rows

    async def name_map(self, moment: datetime | None = None) -> dict[str, str]:
        return {dv.ma_don_vi: dv.display_name for dv in await self.list_all(moment)}

    async def catalog(self) -> list[dict[str, str]]:
        """Mã, tên và mô tả phòng ban để đề xuất nơi xử lý văn bản."""
        return [
            {"ma_phong_ban": dv.ma_don_vi, "ten_phong_ban": dv.display_name,
             "mo_ta": dv.describe or ""}
            for dv in await self.list_all()
        ]
