"""Hồ sơ bị sửa nhiều lần trong kỳ không được đếm thành nhiều lần tuyển mới.

ERP không sửa đè lên hồ sơ cũ: mỗi lần nhập lại, bản cũ ở lại bảng với
`IsDeleted=1` còn bản mới được thêm vào. `headcount_by_dept` lọc đúng nên nhân sự
không sao, nhưng `movement` trước đây đếm thẳng theo `HireDate` mà không lọc, nên
một người được nhập lại hai lần thành ba lần tuyển mới.

Trên CSDL thật (tenant 64, kỳ 2026-09) lỗi này ra 4 người tuyển mới trong khi chỉ
có 2, và làm gãy phép kiểm "nhân sự tăng = tuyển mới - nghỉ việc".
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import get_settings

from conftest import ERP_TENANT

DAU_KY = datetime(2026, 1, 1)


@pytest.fixture
async def erp_ho_so_nhap_lai():
    """Kỳ 2026-09: hai người vào làm, một trong hai bị nhập lại hai lần."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.erp_models import EmployeeProfile, ErpBase, WorkDepartment

    settings = get_settings()
    truoc = settings.erp_tenant_id
    settings.erp_tenant_id = ERP_TENANT

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ErpBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(WorkDepartment(
            id=1, tenant_id=ERP_TENANT, code="00001", department_code="D1",
            display_name="Phòng Kinh doanh", describe="",
            creation_time=DAU_KY, is_deleted=False))

        def nhan_su(emp_id, ten, hire_date, creation_time,
                    deletion_time=None):
            return EmployeeProfile(
                id=emp_id, tenant_id=ERP_TENANT, employee_code=f"NV{emp_id:03d}",
                full_name=ten, hire_date=hire_date, resignation_date=None,
                work_department_id=1, creation_time=creation_time,
                is_deleted=deletion_time is not None, deletion_time=deletion_time)

        # Có sẵn từ đầu năm.
        session.add(nhan_su(1, "Người cũ", DAU_KY, DAU_KY))
        # Vào làm trong kỳ, hồ sơ nhập một lần.
        session.add(nhan_su(2, "Người mới", datetime(2026, 9, 7), datetime(2026, 9, 8)))
        # Cùng một người, hồ sơ nhập ba lần trong ngày 15/9; hai bản đầu bị xoá mềm.
        session.add(nhan_su(3, "Người nhập lại", datetime(2026, 9, 15),
                            datetime(2026, 9, 14, 16, 51),
                            deletion_time=datetime(2026, 9, 15, 8, 35)))
        session.add(nhan_su(4, "Người nhập lại", datetime(2026, 9, 15),
                            datetime(2026, 9, 15, 8, 38),
                            deletion_time=datetime(2026, 9, 15, 8, 46)))
        session.add(nhan_su(5, "Người nhập lại", datetime(2026, 9, 15),
                            datetime(2026, 9, 15, 8, 56)))
        await session.commit()
        yield session

    await engine.dispose()
    settings.erp_tenant_id = truoc


async def test_sql_lich_su_loai_dung_ban_ghi_xoa_mem(erp_ho_so_nhap_lai, erp_query):
    result = await erp_query(erp_ho_so_nhap_lai, """
        SELECT COUNT(*) AS total,
          SUM(CASE WHEN HireDate>='2026-09-01' AND HireDate<'2026-10-01' THEN 1 ELSE 0 END) AS hires
        FROM dbo.Hrm_EmployeeProfile
        WHERE IsDeleted=0 OR DeletionTime>='2026-10-01'
    """)
    assert result.rows == [[3,2]]


async def test_executor_khong_tu_ap_bo_loc_xoa_hien_tai_cho_lich_su(erp_ho_so_nhap_lai, erp_query):
    result = await erp_query(erp_ho_so_nhap_lai, """
        SELECT COUNT(*) AS total FROM dbo.Hrm_EmployeeProfile
        WHERE CreationTime<'2026-09-15' AND (IsDeleted=0 OR DeletionTime>='2026-09-15')
    """)
    assert result.rows == [[3]]
