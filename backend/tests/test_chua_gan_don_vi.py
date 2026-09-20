"""Bản ghi ERP chưa gán phòng ban không được biến mất khỏi phép cộng.

`WHERE WorkDepartmentId IN (...)` không bao giờ khớp `NULL`, nên trước đây trang
bị thiếu phòng ban bị loại khỏi mọi thống kê - im lặng, không lỗi. Trên CSDL thật
90/98 thiết bị rơi vào đúng trường hợp này: báo cáo "toàn công ty" đếm được 8 dòng
trên tổng số 98.

Ranh giới cần giữ, và đây là lý do bài test này tồn tại: hỏi TOÀN CÔNG TY thì cộng
vào, hỏi MỘT SỐ ĐƠN VỊ thì không - không ai biết chúng thuộc đơn vị nào.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import get_settings
from app.tools.data import get_equipment_statistics, get_personnel_statistics

from conftest import ERP_TENANT

DAU_KY = datetime(2026, 1, 1)


@pytest.fixture
async def erp_thieu_phong_ban():
    """Hai đơn vị có thiết bị, cộng thêm thiết bị và nhân sự không thuộc đơn vị nào."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.erp_models import Asset, AssetCategory, EmployeeProfile, ErpBase, WorkDepartment

    settings = get_settings()
    truoc = settings.erp_tenant_id
    settings.erp_tenant_id = ERP_TENANT

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ErpBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(AssetCategory(id=1, tenant_id=ERP_TENANT, code="TB", name="Thiết bị",
                                  is_active=True, creation_time=DAU_KY, is_deleted=False))
        for dept_id, code, name in ((1, "00001", "Đơn vị 1"), (2, "00002", "Đơn vị 2")):
            session.add(WorkDepartment(
                id=dept_id, tenant_id=ERP_TENANT, code=code, department_code=f"D{dept_id}",
                display_name=name, describe="", creation_time=DAU_KY, is_deleted=False))

        # 10 + 5 có chủ, 100 vô chủ - tỷ lệ cố ý lệch như dữ liệu thật.
        for asset_id, name, qty, dept_id in (
            (1, "Máy in", 10, 1), (2, "Máy chủ", 5, 2), (3, "Máy phát điện", 100, None),
        ):
            session.add(Asset(
                id=asset_id, tenant_id=ERP_TENANT, code=f"TS{asset_id:03d}", name=name,
                asset_category_id=1, status=0, quantity=qty, work_department_id=dept_id,
                is_active=True, creation_time=DAU_KY, last_modification_time=DAU_KY,
                is_deleted=False))

        for emp_id, dept_id in ((1, 1), (2, 2), (3, None)):
            session.add(EmployeeProfile(
                id=emp_id, tenant_id=ERP_TENANT, employee_code=f"NV{emp_id:03d}",
                full_name=f"Người {emp_id}", hire_date=DAU_KY, resignation_date=None,
                work_department_id=dept_id, creation_time=DAU_KY, is_deleted=False))
        await session.commit()
        yield session

    await engine.dispose()
    settings.erp_tenant_id = truoc


# --------------------------------------------------------------------------- #
# Thiết bị
# --------------------------------------------------------------------------- #
async def test_toan_cong_ty_dem_ca_thiet_bi_chua_gan(erp_thieu_phong_ban):
    """Trước bản vá: 15. Đúng: 115."""
    result = await get_equipment_statistics(erp_thieu_phong_ban, "2026-09")

    assert result.metrics["total_equipment"].value == 115
    assert result.scope["chua_gan_don_vi"] == 100


async def test_dong_chua_gan_hien_trong_bang_va_nam_cuoi(erp_thieu_phong_ban):
    """Cộng vào tổng mà không hiện ra dòng nào thì người đọc không đối chiếu được."""
    result = await get_equipment_statistics(erp_thieu_phong_ban, "2026-09")

    cuoi = result.breakdown[-1]
    assert cuoi["ten_don_vi"] == "(chưa gán phòng ban)"
    assert cuoi["so_luong"] == 100
    assert sum(row["so_luong"] for row in result.breakdown) == 115


async def test_hoi_mot_don_vi_thi_khong_cong_bua_vao(erp_thieu_phong_ban):
    """Không ai biết 100 máy phát điện thuộc đơn vị nào, nên không được cộng."""
    result = await get_equipment_statistics(erp_thieu_phong_ban, "2026-09",
                                            ma_don_vi="00001")

    assert result.metrics["total_equipment"].value == 10
    assert "chua_gan_don_vi" not in result.scope
    assert all(row["ten_don_vi"] != "(chưa gán phòng ban)" for row in result.breakdown)


async def test_co_canh_bao_khi_du_lieu_erp_thieu_phong_ban(erp_thieu_phong_ban):
    result = await get_equipment_statistics(erp_thieu_phong_ban, "2026-09")

    assert not result.is_consistent
    assert "100/115" in result.consistency[0].message


async def test_units_with_data_khong_dem_nham_dong_vo_chu(erp_thieu_phong_ban):
    """Dòng chưa gán phòng ban không phải là một "đơn vị có dữ liệu"."""
    result = await get_equipment_statistics(erp_thieu_phong_ban, "2026-09")

    assert result.scope["units_with_data"] == 2


# --------------------------------------------------------------------------- #
# Nhân sự - cùng một lỗi, cùng một ranh giới
# --------------------------------------------------------------------------- #
async def test_nhan_su_toan_cong_ty_dem_ca_nguoi_chua_gan(erp_thieu_phong_ban):
    """Nhánh "(chưa gán phòng ban)" của bảng nhân sự trước đây không chạy được:
    bộ lọc `IN (...)` đã loại sạch dòng `NULL` trước khi tới đó."""
    result = await get_personnel_statistics(erp_thieu_phong_ban, "2026-09")

    assert result.metrics["total_personnel"].value == 3
    assert result.breakdown[-1]["ten_don_vi"] == "(chưa gán phòng ban)"
    assert result.breakdown[-1]["nhan_su"] == 1


async def test_nhan_su_mot_don_vi_khong_cong_nguoi_vo_chu(erp_thieu_phong_ban):
    result = await get_personnel_statistics(erp_thieu_phong_ban, "2026-09",
                                            ma_don_vi="00001")

    assert result.metrics["total_personnel"].value == 1
    assert all(row["ten_don_vi"] != "(chưa gán phòng ban)" for row in result.breakdown)
