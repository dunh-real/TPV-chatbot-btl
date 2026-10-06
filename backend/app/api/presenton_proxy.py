"""Đưa giao diện sửa slide của Presenton ra CÙNG MỘT origin với backend.

Presenton chạy trong Docker và chỉ bind `127.0.0.1:5002` (xem `docker/presenton.yml`).
`edit_path` nó trả về là đường dẫn TƯƠNG ĐỐI - `/presentation?id=<uuid>` - nên
trình duyệt ghép vào origin của trang đang mở, tức là của backend. Trước bản này
bấm vào đó rơi thẳng vào 404 của backend, ở cả máy dev lẫn qua Cloudflare.

Hai cách sửa, và vì sao chọn cách này:

  1. Hostname riêng trỏ vào cổng 5002 (thêm một public hostname trong tunnel).
     Không phải viết dòng nào, nhưng Presenton đang chạy `DISABLE_AUTH=true` -
     bật xác thực lên thì bước export tự gọi API của chính nó và nhận 401 - nên
     hostname ấy là cửa mở: ai biết URL cũng đọc và sửa được MỌI bộ slide, trong
     đó có số liệu nhân sự.

  2. Proxy qua chính backend (cách đang dùng). Cùng origin nên đường dẫn tương
     đối chạy đúng ở mọi nơi mà không phải khai thêm URL nào, và quan trọng hơn:
     nó nằm SAU chốt `PUBLIC_ACCESS_TOKEN` như mọi đường khác của ứng dụng.

Không đặt Presenton dưới một tiền tố con được: trang Next.js gọi tài nguyên ở
GỐC (`/_next/...`, `/Logo.png`), muốn dời đi phải sửa `basePath` rồi build lại
image. Nên ở đây chuyển tiếp đúng những tiền tố Presenton thật sự sở hữu, và
danh sách đó là cố định - không phải bắt tất cả những gì backend không nhận.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["presenton"], include_in_schema=False)

# Header do tầng vận chuyển quản, chuyển tiếp nguyên si là hỏng: `content-length`
# sai khi httpx đã giải nén sẵn, `transfer-encoding` thì Starlette tự đặt lại.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "content-encoding", "content-length",
})

# Ảnh/icon nằm thẳng ở gốc trong `public/` của Presenton (Logo.png, favicon.ico...).
# Chỉ nhận đúng những đuôi này để tuyến bắt-gốc không nuốt mất đường nào của
# backend - `/openapi.json` chẳng hạn.
_ASSET_SUFFIXES = (".png", ".svg", ".ico", ".jpg", ".jpeg", ".gif", ".webp",
                   ".woff", ".woff2", ".webmanifest", ".txt")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Một client dùng lại cho mọi lời gọi: mở trang sửa slide là vài chục request
    tài nguyên, dựng client cho từng cái thì bắt tay TCP nhiều hơn cả tải file."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=get_settings().presenton_url.rstrip("/"),
            # `read=None`: SSE giữ kết nối mở tới khi bên kia đóng, đặt hạn đọc
            # là tự cắt giữa chừng một luồng đang chạy bình thường.
            timeout=httpx.Timeout(60.0, connect=5.0, read=None),
            follow_redirects=False,
        )
    return _client


async def close_client() -> None:
    """Gọi lúc tắt ứng dụng. Không đóng thì uvicorn kêu 'Unclosed connection'."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _forward(request: Request, path: str) -> Response:
    """Chuyển tiếp một request sang Presenton, trả về theo DÒNG.

    Đọc hết thân rồi mới trả thì hỏng hai chỗ: `/api/v1/ppt/presentation/stream/{id}`
    là SSE - upstream không bao giờ đóng, nên trình duyệt chờ mãi không nhận được
    sự kiện nào; và file .pptx xuất ra bị giữ nguyên trong RAM một lần nữa.
    """
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and k.lower() != "host"}
    # Không nhận nén: hai bên nằm trên cùng một máy, giải nén rồi nén lại chỉ tốn
    # CPU - và `aiter_raw` trả đúng byte gốc nên không được để upstream gzip.
    headers["accept-encoding"] = "identity"

    client = _get_client()
    upstream_request = client.build_request(
        request.method, path,
        params=request.query_params,
        content=await request.body(),
        headers=headers,
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        logger.warning("Không nối được Presenton (%s): %s", path, exc)
        raise HTTPException(
            status_code=502,
            detail="Không nối được Presenton. Container còn chạy không? "
                   "docker compose --env-file .env -f docker/presenton.yml up -d",
        ) from exc

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers={k: v for k, v in upstream.headers.items()
                 if k.lower() not in _HOP_BY_HOP},
        media_type=upstream.headers.get("content-type"),
        # Phải đóng phản hồi upstream sau khi gửi xong, nếu không kết nối tới
        # Presenton bị giữ lại cho tới khi hết timeout.
        background=BackgroundTask(upstream.aclose),
    )


@router.api_route("/presentation", methods=["GET", "HEAD"])
async def editor(request: Request) -> Response:
    """Trang sửa bộ slide - đích của `edit_path`."""
    return await _forward(request, "/presentation")


@router.api_route("/_next/{path:path}", methods=["GET", "HEAD"])
async def next_assets(path: str, request: Request) -> Response:
    return await _forward(request, f"/_next/{path}")


@router.api_route("/static/{path:path}", methods=["GET", "HEAD"])
async def static_files(path: str, request: Request) -> Response:
    return await _forward(request, f"/static/{path}")


@router.api_route("/app_data/{path:path}", methods=["GET", "HEAD"])
async def app_data(path: str, request: Request) -> Response:
    """Ảnh và file bộ slide do Presenton sinh ra."""
    return await _forward(request, f"/app_data/{path}")


@router.api_route("/api/v1/{path:path}",
                  methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def presenton_api(path: str, request: Request) -> Response:
    """API riêng của Presenton mà trình sửa gọi. Không đụng tới `/api/...` của
    backend: các router kia đã khai trước và dùng tiền tố khác."""
    return await _forward(request, f"/api/v1/{path}")


@router.api_route("/{asset}", methods=["GET", "HEAD"])
async def root_asset(asset: str, request: Request) -> Response:
    """Ảnh/icon ở gốc. Tuyến này đăng ký SAU cùng và chỉ nhận vài đuôi file, nên
    không che mất đường nào của backend."""
    if not asset.lower().endswith(_ASSET_SUFFIXES):
        raise HTTPException(status_code=404, detail="Not Found")
    return await _forward(request, f"/{asset}")
