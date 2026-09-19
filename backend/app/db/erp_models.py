"""Ánh xạ các bảng ERP được phép đọc.

`ErpBase` tách riêng khỏi `app.db.models.Base` một cách có chủ ý: `create_all` của
CSDL app sẽ không bao giờ nhìn thấy các bảng này, nên không có đường nào để hệ
thống tự tạo hay sửa cấu trúc bảng ERP.

Chỉ khai báo những cột thực sự dùng tới. Bảng ERP có hàng chục cột quản trị
(CreatorUserId, TextSearch, QrCode...) mà không workflow nào cần.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Unicode
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ErpBase(DeclarativeBase):
    pass


class WorkDepartment(ErpBase):
    """`Dms_WorkDepartment` - phòng ban, đóng vai "đơn vị" trong các báo cáo."""

    __tablename__ = "Dms_WorkDepartment"

    id: Mapped[int] = mapped_column("Id", BigInteger, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column("TenantId", Integer)
    parent_id: Mapped[int | None] = mapped_column("ParentId", BigInteger)
    code: Mapped[str] = mapped_column("Code", Unicode(95))
    department_code: Mapped[str] = mapped_column("DepartmentCode", Unicode(95))
    display_name: Mapped[str] = mapped_column("DisplayName", Unicode(512))
    describe: Mapped[str | None] = mapped_column("Describe", Unicode)
    creation_time: Mapped[datetime] = mapped_column("CreationTime", DateTime)
    is_deleted: Mapped[bool] = mapped_column("IsDeleted", Boolean)
    deletion_time: Mapped[datetime | None] = mapped_column("DeletionTime", DateTime)

    @property
    def ma_don_vi(self) -> str:
        """Mã hiển thị cho người dùng. `Code` là mã có thứ tự ("00001"), dễ đọc và
        dễ gõ hơn `DepartmentCode` vốn là chuỗi sinh ngẫu nhiên."""
        return self.code or self.department_code


class AssetCategory(ErpBase):
    """`Asm_AssetCategories` - chủng loại trang bị."""

    __tablename__ = "Asm_AssetCategories"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column("TenantId", Integer)
    code: Mapped[str] = mapped_column("Code", Unicode(95))
    name: Mapped[str] = mapped_column("Name", Unicode(512))
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean)
    creation_time: Mapped[datetime] = mapped_column("CreationTime", DateTime)
    is_deleted: Mapped[bool] = mapped_column("IsDeleted", Boolean)
    deletion_time: Mapped[datetime | None] = mapped_column("DeletionTime", DateTime)


class Asset(ErpBase):
    """`Asm_Assets` - một trang bị thuộc một phòng ban.

    `Quantity` cho phép NULL trong khi báo cáo luôn phải cộng ra một con số; đọc
    qua `so_luong` để một dòng thiếu số lượng được tính là 1 chứ không phải 0.
    """

    __tablename__ = "Asm_Assets"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column("TenantId", Integer)
    code: Mapped[str] = mapped_column("Code", Unicode(50))
    name: Mapped[str] = mapped_column("Name", Unicode(256))
    asset_category_id: Mapped[int | None] = mapped_column("AssetCategoryId", Integer)
    status: Mapped[int] = mapped_column("Status", Integer)
    quantity: Mapped[int | None] = mapped_column("Quantity", Integer)
    current_location: Mapped[str | None] = mapped_column("CurrentLocation", Unicode(256))
    work_department_id: Mapped[int | None] = mapped_column("WorkDepartmentId", BigInteger)
    serial_number: Mapped[str | None] = mapped_column("SerialNumber", Unicode(100))
    purchase_date: Mapped[datetime | None] = mapped_column("PurchaseDate", DateTime)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean)
    creation_time: Mapped[datetime] = mapped_column("CreationTime", DateTime)
    last_modification_time: Mapped[datetime | None] = mapped_column(
        "LastModificationTime", DateTime
    )
    is_deleted: Mapped[bool] = mapped_column("IsDeleted", Boolean)
    deletion_time: Mapped[datetime | None] = mapped_column("DeletionTime", DateTime)

    @property
    def so_luong(self) -> int:
        return 1 if self.quantity is None else self.quantity


class EmployeeProfile(ErpBase):
    """`Hrm_EmployeeProfile` - hồ sơ nhân sự, nguồn của chỉ tiêu quân số.

    Quân số tại một thời điểm suy ra từ `HireDate`/`ResignationDate`. Không dùng
    `EmploymentStatusId` vì bảng tra `Hrm_EmployeeStatus` nằm ngoài danh sách bảng
    được phép đọc, nên con số sẽ không giải thích được bằng dữ liệu nhìn thấy.
    """

    __tablename__ = "Hrm_EmployeeProfile"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column("TenantId", Integer)
    employee_code: Mapped[str] = mapped_column("EmployeeCode", Unicode(95))
    full_name: Mapped[str] = mapped_column("FullName", Unicode(512))
    hire_date: Mapped[datetime | None] = mapped_column("HireDate", DateTime)
    official_date: Mapped[datetime | None] = mapped_column("OfficialDate", DateTime)
    resignation_date: Mapped[datetime | None] = mapped_column("ResignationDate", DateTime)
    work_department_id: Mapped[int | None] = mapped_column("WorkDepartmentId", BigInteger)
    work_position_id: Mapped[int | None] = mapped_column("WorkPositionId", Integer)
    creation_time: Mapped[datetime] = mapped_column("CreationTime", DateTime)
    is_deleted: Mapped[bool] = mapped_column("IsDeleted", Boolean)
    deletion_time: Mapped[datetime | None] = mapped_column("DeletionTime", DateTime)
