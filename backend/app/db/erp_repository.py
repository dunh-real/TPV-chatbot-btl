"""Truy vấn số liệu nghiệp vụ từ ERP (chỉ đọc).

ERP không lưu ảnh chụp theo kỳ: `Asm_Assets` và `Hrm_EmployeeProfile` chỉ giữ
hiện trạng kèm mốc thời gian tạo/xoá. Nên số liệu của một kỳ ở đây được DỰNG LẠI
bằng truy vấn "as-of": lấy những dòng đã tồn tại trước thời điểm chốt kỳ và chưa
bị xoá tính tới thời điểm đó.

Hệ quả cần biết khi đọc báo cáo: đây là hiện trạng suy ngược, không phải con số
đã chốt tại kỳ. Một trang bị bị sửa số lượng sau kỳ sẽ làm số của kỳ cũ đổi theo.
Mọi hàm ở đây vì thế trả kèm `ghi_chu` nói rõ điều đó, và `assumptions` của
workflow sẽ nhắc lại trong văn bản cuối.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import current_tenant_id
from app.db.erp_models import Asset, AssetCategory, EmployeeProfile, WorkDepartment
from app.db.repository import TaiNguyenDonVi

logger = logging.getLogger(__name__)

AS_OF_NOTE = ("Số liệu kỳ được dựng lại từ hiện trạng ERP theo mốc thời gian tạo/xoá "
              "bản ghi, không phải số đã chốt tại kỳ.")


def period_end(ky: str) -> datetime:
    """Mốc chốt kỳ "YYYY-MM": 0h ngày đầu tháng kế tiếp (biên phải, không bao gồm)."""
    year, month = int(ky[:4]), int(ky[5:])
    return datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)


def period_start(ky: str) -> datetime:
    return datetime(int(ky[:4]), int(ky[5:]), 1)


def _alive_at(model: Any, moment: datetime) -> Any:
    """Dòng đã tồn tại và chưa bị xoá mềm tính tới `moment`."""
    return (
        (model.creation_time < moment)
        # `== False` chứ không phải `.is_(False)`: cột là BIT, SQL Server cần "= 0".
        & (
            (model.is_deleted == False)  # noqa: E712
            | (model.deletion_time >= moment)
        )
    )


def _thuoc_don_vi(model: Any, dept_ids: list[int], *, gom_chua_gan: bool) -> Any:
    """Lọc theo phòng ban, có kèm hay không kèm những dòng CHƯA gán phòng ban.

    `IN (...)` không bao giờ khớp `NULL`. Bản ghi ERP thiếu `WorkDepartmentId` vì
    thế biến mất hoàn toàn khỏi mọi phép cộng - im lặng, không lỗi, không cảnh báo.
    Trên dữ liệu thật đó không phải trường hợp hiếm: 90/98 trang bị chưa gán phòng
    ban, tức báo cáo "toàn cơ quan" từng chỉ đếm được 8 dòng.

    Hỏi toàn cơ quan thì những dòng ấy vẫn là tài sản của cơ quan, phải cộng vào.
    Hỏi vài đơn vị cụ thể thì không: không ai biết chúng thuộc đơn vị nào, cộng
    vào là gán bừa.
    """
    thuoc = model.work_department_id.in_(dept_ids)
    return or_(thuoc, model.work_department_id.is_(None)) if gom_chua_gan else thuoc


def _tenant_scoped(stmt: Select, model: Any) -> Select:
    """ERP chạy đa tenant; thiếu bộ lọc này là cộng nhầm số của thuê bao khác.

    Tenant lấy theo TỪNG REQUEST (`app.core.context`), không phải một hằng số toàn
    tiến trình: cùng một tiến trình sẽ phục vụ nhiều đơn vị khi giao diện có đăng
    nhập. Ngoài ngữ cảnh HTTP - script, tác vụ nền, test - giá trị rơi về
    `ERP_TENANT_ID` trong cấu hình.
    """
    tenant_id = current_tenant_id()
    return stmt if tenant_id is None else stmt.where(model.tenant_id == tenant_id)


class ErpDonViRepository:
    """Phòng ban ERP đóng vai "đơn vị" của các báo cáo."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self, moment: datetime | None = None) -> list[WorkDepartment]:
        stmt = select(WorkDepartment).where(_alive_at(WorkDepartment, moment or datetime.now()))
        stmt = _tenant_scoped(stmt, WorkDepartment).order_by(WorkDepartment.code)
        return list((await self.session.execute(stmt)).scalars())

    async def name_map(self, moment: datetime | None = None) -> dict[str, str]:
        return {dv.ma_don_vi: dv.display_name for dv in await self.list_all(moment)}

    async def id_map(self, moment: datetime | None = None) -> dict[str, int]:
        """Mã hiển thị -> khoá chính, để nối sang trang bị / nhân sự."""
        return {dv.ma_don_vi: dv.id for dv in await self.list_all(moment)}

    async def get(self, ma_don_vi: str) -> WorkDepartment | None:
        for dv in await self.list_all():
            if dv.ma_don_vi == ma_don_vi:
                return dv
        return None

    async def catalog(self) -> list[dict[str, str]]:
        """Danh mục phòng ban cho bước đề xuất nơi xử lý văn bản (workflow 2).

        `Describe` của ERP thường để trống, trong khi đây lại là trường quyết định
        chất lượng định tuyến - chỉ có mã và tên thì LLM không suy ra được phòng
        nào phụ trách việc gì.
        """
        return [
            {"ma_phong_ban": dv.ma_don_vi, "ten_phong_ban": dv.display_name,
             "mo_ta": dv.describe or ""}
            for dv in await self.list_all()
        ]


class ErpTrangBiRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_as_of(
        self,
        moment: datetime,
        dept_ids: list[int] | None = None,
        *,
        gom_chua_gan: bool = False,
    ) -> list[tuple[int | None, Asset, str | None]]:
        """(mã phòng ban dạng khoá, trang bị, tên chủng loại) tại thời điểm `moment`.

        `gom_chua_gan=True` lấy thêm trang bị chưa gán phòng ban - xem `_thuoc_don_vi`.
        """
        stmt = (
            select(Asset, AssetCategory.name)
            .join(AssetCategory, AssetCategory.id == Asset.asset_category_id, isouter=True)
            .where(_alive_at(Asset, moment))
        )
        if dept_ids is not None:
            stmt = stmt.where(_thuoc_don_vi(Asset, dept_ids, gom_chua_gan=gom_chua_gan))
        stmt = _tenant_scoped(stmt, Asset).order_by(Asset.name)
        rows = await self.session.execute(stmt)
        return [(asset.work_department_id, asset, category) for asset, category in rows]


class ErpNhanSuRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _in_service_at(self, moment: datetime) -> Any:
        """Đang trong biên chế tại `moment`.

        `HireDate` trống thì lấy `CreationTime` thay: hồ sơ nhập thiếu ngày vào làm
        vẫn phải được đếm, bỏ qua thì quân số hụt mà không ai biết vì sao.
        """
        hired = func.coalesce(EmployeeProfile.hire_date, EmployeeProfile.creation_time)
        return (
            (hired < moment)
            & or_(
                EmployeeProfile.resignation_date.is_(None),
                EmployeeProfile.resignation_date >= moment,
            )
            & (
                (EmployeeProfile.is_deleted == False)  # noqa: E712
                | (EmployeeProfile.deletion_time >= moment)
            )
        )

    async def headcount_by_dept(
        self,
        moment: datetime,
        dept_ids: list[int] | None = None,
        *,
        gom_chua_gan: bool = False,
    ) -> dict[int | None, int]:
        stmt = (
            select(EmployeeProfile.work_department_id, func.count())
            .where(self._in_service_at(moment))
            .group_by(EmployeeProfile.work_department_id)
        )
        if dept_ids is not None:
            stmt = stmt.where(
                _thuoc_don_vi(EmployeeProfile, dept_ids, gom_chua_gan=gom_chua_gan))
        stmt = _tenant_scoped(stmt, EmployeeProfile)
        return {dept_id: count for dept_id, count in await self.session.execute(stmt)}

    async def movement(
        self,
        start: datetime,
        end: datetime,
        dept_ids: list[int] | None = None,
        *,
        gom_chua_gan: bool = False,
    ) -> dict[str, dict[int | None, int]]:
        """Tuyển mới / nghỉ việc trong khoảng [start, end)."""

        async def count_by_dept(column) -> dict[int | None, int]:
            stmt = (
                select(EmployeeProfile.work_department_id, func.count())
                .where(column >= start, column < end)
                .group_by(EmployeeProfile.work_department_id)
            )
            if dept_ids is not None:
                stmt = stmt.where(
                    _thuoc_don_vi(EmployeeProfile, dept_ids, gom_chua_gan=gom_chua_gan))
            stmt = _tenant_scoped(stmt, EmployeeProfile)
            return {dept_id: count for dept_id, count in await self.session.execute(stmt)}

        return {
            "tuyen_moi": await count_by_dept(EmployeeProfile.hire_date),
            "nghi_viec": await count_by_dept(EmployeeProfile.resignation_date),
        }


class ErpTaiNguyenRepository:
    """Ghép phòng ban + trang bị + quân số thành `TaiNguyenDonVi` mà workflow 3 dùng."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.don_vi = ErpDonViRepository(session)
        self.trang_bi = ErpTrangBiRepository(session)
        self.nhan_su = ErpNhanSuRepository(session)

    async def list_don_vi(self) -> list[dict[str, str]]:
        return [
            {"ma_don_vi": dv.ma_don_vi, "ten_don_vi": dv.display_name,
             "mo_ta": dv.describe or ""}
            for dv in await self.don_vi.list_all()
        ]

    async def get_tai_nguyen(
        self, ma_don_vi: str, ky: str | None = None
    ) -> TaiNguyenDonVi | None:
        moment = period_end(ky) if ky else datetime.now()
        don_vi = await self.don_vi.get(ma_don_vi)
        if don_vi is None:
            return None

        rows = await self.trang_bi.list_as_of(moment, [don_vi.id])
        settings = get_settings()
        labels, good_codes = settings.asset_status_labels, settings.asset_status_good
        trang_bi = [
            {
                "ten_trang_bi": asset.name,
                "so_luong": asset.so_luong,
                # Chưa khai báo enum thì hiện mã thô - đọc là biết chưa cấu hình,
                # còn hơn gán đại một nhãn rồi người đọc tin là thật.
                "tinh_trang": labels.get(asset.status, f"Trạng thái {asset.status}"),
                "tinh_trang_tot": (asset.status in good_codes) if good_codes else None,
                "bao_duong_cuoi": asset.last_modification_time.date().isoformat()
                if asset.last_modification_time else None,
                "ma_trang_bi": asset.code,
                "chung_loai": category or "",
                "vi_tri": asset.current_location or "",
            }
            for _, asset, category in rows
        ]

        headcount = await self.nhan_su.headcount_by_dept(moment, [don_vi.id])
        return TaiNguyenDonVi(
            ma_don_vi=don_vi.ma_don_vi,
            ten_don_vi=don_vi.display_name,
            quan_so=headcount.get(don_vi.id, 0),
            quan_so_kiem_ke=_as_date(moment),
            trang_bi=trang_bi,
            ky=ky,
            ghi_chu=AS_OF_NOTE,
        )


def _as_date(moment: datetime) -> date:
    """Mốc chốt kỳ là 0h ngày đầu tháng sau; ngày cần hiển thị là ngày cuối kỳ."""
    return (moment - datetime.resolution).date()
