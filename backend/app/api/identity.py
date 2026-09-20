"""Gắn danh tính vào từng request HTTP.

Đây là middleware ASGI thuần chứ không phải `BaseHTTPMiddleware`: `BaseHTTPMiddleware`
chạy ứng dụng phía dưới trong một task khác, nên `ContextVar` đặt ở đó không đi
xuống tới endpoint một cách chắc chắn. Viết thẳng ở tầng ASGI thì handler chạy
NGAY TRONG ngữ cảnh đã đặt, và điều đó đúng cho cả `/chat/stream` - streaming chỉ
là một `send` kéo dài, không phải một task mới.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import dataclasses

from app.core.context import IdentityError, resolve_principal, use_principal
from app.services.access import resolve_access

logger = logging.getLogger(__name__)


class IdentityMiddleware:
    """Đọc danh tính từ request và đặt vào ngữ cảnh cho toàn bộ lời gọi bên dưới."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        try:
            principal = await _kem_quyen(resolve_principal(headers))
        except IdentityError as exc:
            await JSONResponse(status_code=400, content={"detail": str(exc)})(
                scope, receive, send
            )
            return

        with use_principal(principal):
            logger.debug("%s %s - %s", scope.get("method"), scope.get("path"),
                         principal.describe())
            await self.app(scope, receive, send)


async def _kem_quyen(principal):
    """Gắn quyền đọc từ ERP vào danh tính, và lấy luôn tenant của tài khoản.

    Tenant phải theo TÀI KHOẢN chứ không theo header: người demo chọn một tài
    khoản thuộc tenant khác với `ERP_TENANT_ID` mặc định thì số liệu họ thấy phải
    là của tenant tài khoản đó. Để lệch hai thứ này là cách chắc chắn nhất để
    người dùng đọc số liệu của công ty khác mà không ai nhận ra.
    """
    if not principal.user_id:
        return principal
    access = await resolve_access(principal.user_id)
    if access is None:
        return principal
    return dataclasses.replace(
        principal,
        access=access,
        tenant_id=access.tenant_id if access.tenant_id is not None else principal.tenant_id,
    )
