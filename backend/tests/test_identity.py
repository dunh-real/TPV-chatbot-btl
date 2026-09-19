"""Danh tính theo request: tenant đúng đi tới tận câu SQL, sai thì bị chặn.

Điều đáng kiểm ở đây không phải là middleware có chạy hay không, mà là giá trị nó
đặt có THẬT SỰ thay đổi câu truy vấn ERP hay không. Một bài test chỉ đọc lại
`current_principal()` sẽ xanh cả khi `_tenant_scoped` quên dùng tới nó.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api.identity import IdentityMiddleware
from app.core.config import get_settings
from app.core.context import Principal, current_principal, use_principal
from app.db.erp_repository import ErpDonViRepository, ErpNhanSuRepository, period_end

from conftest import ERP_TENANT


@pytest.fixture
def api():
    """App tối giản: chỉ middleware danh tính, không kéo theo Qdrant/LLM."""
    app = FastAPI()
    app.add_middleware(IdentityMiddleware)

    @app.get("/whoami")
    async def whoami() -> dict:
        principal = current_principal()
        return {"tenant_id": principal.tenant_id, "user_id": principal.user_id,
                "source": principal.source}

    return app


async def _get(app: FastAPI, **headers: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/whoami", headers=headers)


# --------------------------------------------------------------------------- #
# Lấy danh tính từ đâu
# --------------------------------------------------------------------------- #
async def test_khong_khai_gi_thi_dung_mac_dinh(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    body = (await _get(api)).json()
    assert body == {"tenant_id": 64, "user_id": None, "source": "default"}


async def test_header_de_len_mac_dinh(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    body = (await _get(api, **{"X-Tenant-Id": "67", "X-User-Id": "nguyenvana"})).json()
    assert body == {"tenant_id": 67, "user_id": "nguyenvana", "source": "header"}


async def test_chi_khai_user_thi_tenant_van_la_mac_dinh(api, monkeypatch):
    """Giao diện gửi user mà chưa gửi tenant không được làm mất bộ lọc tenant."""
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    body = (await _get(api, **{"X-User-Id": "nguyenvana"})).json()
    assert body["tenant_id"] == 64
    assert body["user_id"] == "nguyenvana"


async def test_tenant_sai_dinh_dang_bi_chan(api, monkeypatch):
    """Không được lặng lẽ rơi về mặc định: đó là cách trả nhầm số liệu đơn vị khác."""
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    response = await _get(api, **{"X-Tenant-Id": "abc"})
    assert response.status_code == 400
    assert "x-tenant-id" in response.json()["detail"].lower()


async def test_tat_tin_header_thi_bo_qua_loi_khai(api, monkeypatch):
    """Công tắc cho lúc có đăng nhập: header hết trọng lượng, chỉ còn token."""
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    monkeypatch.setattr(get_settings(), "trust_identity_headers", False)
    body = (await _get(api, **{"X-Tenant-Id": "67", "X-User-Id": "kegiamao"})).json()
    assert body == {"tenant_id": 64, "user_id": None, "source": "default"}


async def test_ngu_canh_khong_ro_ri_sang_request_sau(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "erp_tenant_id", 64)
    assert (await _get(api, **{"X-Tenant-Id": "67"})).json()["tenant_id"] == 67
    assert (await _get(api)).json()["tenant_id"] == 64


# --------------------------------------------------------------------------- #
# Danh tính có tới được câu SQL không
# --------------------------------------------------------------------------- #
async def test_tenant_cua_request_loc_that_o_tang_truy_van(erp_session):
    """Cùng một phiên, đổi tenant trong ngữ cảnh là đổi kết quả đọc ra."""
    don_vi = ErpDonViRepository(erp_session)

    with use_principal(Principal(tenant_id=ERP_TENANT, source="header")):
        assert len(await don_vi.list_all()) == 2

    # Tenant khác: dữ liệu mẫu không thuộc về nó nên không được thấy dòng nào.
    with use_principal(Principal(tenant_id=ERP_TENANT + 1, source="header")):
        assert await don_vi.list_all() == []
        assert await ErpNhanSuRepository(erp_session).headcount_by_dept(
            period_end("2026-09")) == {}

    # Không lọc tenant: quay lại nhìn thấy đủ.
    with use_principal(Principal(tenant_id=None, source="header")):
        assert len(await don_vi.list_all()) == 2


async def test_ngoai_http_thi_roi_ve_cau_hinh(erp_session, monkeypatch):
    """Script và tác vụ nền không có request nào, vẫn phải lọc đúng tenant."""
    monkeypatch.setattr(get_settings(), "erp_tenant_id", ERP_TENANT)
    assert current_principal().source == "default"
    assert len(await ErpDonViRepository(erp_session).list_all()) == 2

    monkeypatch.setattr(get_settings(), "erp_tenant_id", ERP_TENANT + 1)
    assert await ErpDonViRepository(erp_session).list_all() == []
