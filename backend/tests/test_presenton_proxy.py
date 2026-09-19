"""Trang sửa slide của Presenton phải mở được từ chính origin của backend.

`edit_path` Presenton trả về là đường dẫn TƯƠNG ĐỐI (`/presentation?id=...`), nên
trình duyệt ghép nó vào origin của trang đang mở - tức của backend. Không có
proxy thì cái nút "Sửa tiếp trong Presenton" rơi vào 404 của chính backend.

Hai ranh giới bài test này giữ:

  1. Proxy chỉ được nhận những tiền tố Presenton sở hữu. Nó có một tuyến bắt ảnh
     ở GỐC (`/{asset}`) - đăng ký nhầm chỗ là nó đứng chắn `/health`, `/docs`,
     `/openapi.json`, và lỗi ấy chỉ lộ ra khi có người mở trang.
  2. Đường `/api/v1/*` của Presenton không được lẫn với `/api/...` của backend.
"""

from __future__ import annotations

import httpx
import pytest

from app.api import presenton_proxy
from app.main import app


@pytest.fixture
def presenton(monkeypatch):
    """Presenton giả, ghi lại request nhận được - không cần container nào chạy."""
    nhan: list[httpx.Request] = []

    async def than():
        # Thân dạng dòng chứ không phải chuỗi sẵn: proxy chuyển tiếp bằng
        # `aiter_raw()`, mà một `Response` dựng từ `text=` thì đã đọc xong nội
        # dung - bài test sẽ đỏ vì cách dựng mock, không phải vì proxy sai.
        yield "<html>trang sửa slide</html>".encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        nhan.append(request)
        return httpx.Response(200, content=than(),
                              headers={"content-type": "text/html"})

    monkeypatch.setattr(
        presenton_proxy, "_client",
        httpx.AsyncClient(transport=httpx.MockTransport(handler),
                          base_url="http://presenton.test"),
    )
    return nhan


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def test_trang_sua_slide_di_toi_presenton(presenton):
    res = await _get("/presentation?id=abc-123")

    assert res.status_code == 200
    assert len(presenton) == 1
    assert presenton[0].url.path == "/presentation"
    assert presenton[0].url.params["id"] == "abc-123"


async def test_tai_nguyen_next_va_anh_goc_di_qua(presenton):
    await _get("/_next/static/chunks/abc.js")
    await _get("/Logo.png")

    assert [r.url.path for r in presenton] == ["/_next/static/chunks/abc.js", "/Logo.png"]


async def test_api_cua_presenton_di_qua(presenton):
    await _get("/api/v1/ppt/presentation/abc-123")

    assert presenton[0].url.path == "/api/v1/ppt/presentation/abc-123"


@pytest.mark.parametrize("path", ["/openapi.json", "/docs"])
async def test_duong_cua_backend_khong_bi_proxy_nuot(presenton, path):
    """Tuyến bắt ảnh ở gốc đăng ký sau cùng, nên những đường này vẫn về backend."""
    res = await _get(path)

    assert res.status_code == 200
    assert presenton == [], f"{path} bị đẩy sang Presenton"


async def test_duong_khong_phai_anh_khong_lam_phien_presenton(presenton):
    """Gõ nhầm URL thì trả 404 tại chỗ, không mở kết nối sang Presenton."""
    res = await _get("/khong-ton-tai")

    assert res.status_code == 404
    assert presenton == []


async def test_api_cua_backend_khong_bi_day_sang_presenton(presenton):
    """`/api/v1/*` là của Presenton, `/api/agent/*` là của backend - không lẫn."""
    await _get("/api/agent/tools")

    assert presenton == []
