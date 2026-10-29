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


@pytest.mark.parametrize("where,expected", [("",115), ("WHERE WorkDepartmentId=1",10)])
async def test_sql_dem_dung_pham_vi_thiet_bi(erp_thieu_phong_ban, erp_query, where, expected):
    result = await erp_query(erp_thieu_phong_ban, f"SELECT SUM(Quantity) AS total FROM dbo.Asm_Assets {where}")
    assert result.rows == [[expected]]


async def test_nhom_null_khong_bi_executor_loai_bo(erp_thieu_phong_ban, erp_query):
    result = await erp_query(erp_thieu_phong_ban, "SELECT WorkDepartmentId,SUM(Quantity) AS total FROM dbo.Asm_Assets GROUP BY WorkDepartmentId")
    assert [None,100] in result.rows
    assert sum(row[1] for row in result.rows) == 115


@pytest.mark.parametrize("where,expected", [("",3), ("WHERE WorkDepartmentId=1",1)])
async def test_sql_dem_dung_pham_vi_nhan_su(erp_thieu_phong_ban, erp_query, where, expected):
    result = await erp_query(erp_thieu_phong_ban, f"SELECT COUNT(*) AS total FROM dbo.Hrm_EmployeeProfile {where}")
    assert result.rows == [[expected]]
