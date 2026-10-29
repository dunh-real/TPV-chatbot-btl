"""`/health/request` - soi request server nhận được.

Endpoint này tồn tại vì một lý do cụ thể: CORS bị chặn thì trình duyệt KHÔNG
trả mã lỗi nào cho JavaScript, mọi nguyên nhân đều hiện ra là "Failed to fetch".
Nên phần đáng test nhất không phải "có trả 200 không", mà là "có nói đúng
nguyên nhân không" và "có rò cấu hình ra ngoài không".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    s = get_settings()
    monkeypatch.setattr(s, "cors_origins",
                        ["http://localhost:5173", "https://bqp-ai.tpvtech.vn"])
    return TestClient(app)


def test_origin_duoc_phep_thi_noi_khong_chan(client):
    r = client.get("/health/request", headers={"Origin": "https://bqp-ai.tpvtech.vn"})
    assert r.status_code == 200
    d = r.json()
    assert d["origin"] == "https://bqp-ai.tpvtech.vn"
    assert d["cors_cho_phep"] is True
    assert any("không chặn" in c for c in d["chuan_doan"])


def test_origin_bi_chan_thi_chi_ro_phai_them_chuoi_nao(client):
    r = client.get("/health/request", headers={"Origin": "https://la.example.com"})
    d = r.json()
    assert d["cors_cho_phep"] is False
    loi = " ".join(d["chuan_doan"])
    # Phải nêu ĐÚNG chuỗi cần thêm - đây là thứ người gỡ lỗi chép đi.
    assert "https://la.example.com" in loi and "CORS_ORIGINS" in loi


def test_khong_co_origin_thi_khong_ket_luan_ve_cors(client):
    """curl/Postman không gửi Origin. Kết luận "bị chặn" ở đây là báo oan."""
    d = client.get("/health/request").json()
    assert d["origin"] is None
    assert d["cors_cho_phep"] is None
    assert any("không phải gọi từ trang web" in c for c in d["chuan_doan"])


def test_trang_https_goi_api_http_thi_canh_bao_mixed_content(client):
    d = client.get("/health/request",
                   headers={"Origin": "https://bqp-ai.tpvtech.vn",
                            "X-Forwarded-Proto": "http"}).json()
    assert any("mixed content" in c for c in d["chuan_doan"])


def test_qua_proxy_thi_lay_ip_that_va_khong_coi_la_noi_bo(client):
    """Cloudflare nối vào loopback, nên `request.client` luôn là 127.0.0.1.

    Chỉ nhìn nó thì mọi khách Internet đều bị coi là gọi từ chính máy chủ - và
    danh sách origin sẽ bị trả ra ngoài.
    """
    d = client.get("/health/request",
                   headers={"Origin": "https://bqp-ai.tpvtech.vn",
                            "CF-Connecting-IP": "203.0.113.9"}).json()
    assert d["qua_proxy"] is True
    assert d["client_ip"] == "203.0.113.9"
    assert d["cors_da_khai"] is None, "Không được lộ danh sách origin ra ngoài Internet"


def test_goi_tu_chinh_may_chu_thi_thay_cau_hinh(client):
    d = client.get("/health/request").json()
    assert d["cors_da_khai"] == ["http://localhost:5173", "https://bqp-ai.tpvtech.vn"]


@pytest.mark.parametrize("hong", [
    "https://bqp-ai.tpvtech.vn/",        # dấu / ở cuối - đã gõ nhầm thật
    "https://<bqp-ai.tpvtech.vn>",       # chép nguyên chỗ viết mẫu - đã gõ nhầm thật
    "https://bqp-ai.tpvtech.vn/chat",    # kèm đường dẫn
    " https://bqp-ai.tpvtech.vn",        # thừa khoảng trắng
])
def test_bat_duoc_dong_cau_hinh_sai_dinh_dang(client, monkeypatch, hong):
    """Dòng sai định dạng không bao giờ khớp, và hỏng HOÀN TOÀN im lặng."""
    monkeypatch.setattr(get_settings(), "cors_origins", ["http://localhost:5173", hong])
    d = client.get("/health/request").json()
    assert any("sai định dạng" in c for c in d["chuan_doan"]), d["chuan_doan"]


def test_khong_doi_lai_header_nhay_cam(client):
    """API mở ra Internet và chưa có chốt token - dội header là chỗ rò."""
    body = client.get("/health/request", headers={
        "Origin": "https://bqp-ai.tpvtech.vn",
        "Cookie": "tpv_access=BIMAT",
        "Authorization": "Bearer BIMAT",
    }).text
    assert "BIMAT" not in body
