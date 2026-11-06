"""Biên API tích hợp: request ID, snapshot Agent và lỗi JSON/SSE thống nhất."""
import json
import logging
import secrets
import uuid

from starlette.responses import JSONResponse

from app.core import runtime
from app.core.config import get_settings
from app.services.agent_config import activate, load
from app.services.integration_errors import IntegrationError, classify, envelope


def route_agent(path):
    for prefix, code in (("/api/chat/", "qa"), ("/api/agent/chat", "agent"),
        ("/api/documents/summarize", "summarize"), ("/api/documents/review", "review"),
        ("/api/reports/draft", "draft"), ("/api/reports/aggregate", "aggregate"),
        ("/api/presentations/", "slide"), ("/api/document-routings/", "routing"),
        ("/api/mindmap", "mindmap"), ("/api/ocr", "ocr")):
        if path.startswith(prefix):
            return code


def http_error(status, message):
    code = {401: "UNAUTHORIZED", 403: "PARTITION_DENIED", 413: "CONTEXT_TOO_LONG",
            429: "OVERLOADED", 504: "TIMEOUT"}.get(status, "INVALID_REQUEST" if status < 500 else "INTERNAL_ERROR")
    return IntegrationError(code, str(message) if status < 500 else "Dịch vụ gặp sự cố; kiểm tra log theo requestId", status)


class IntegrationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin1") for k, v in scope["headers"]}
        supplied = headers.get("x-request-id")
        rid = supplied or str(uuid.uuid4())
        token = runtime.request_id.set(rid)
        error_token = runtime.last_error.set(None)
        filter_token = None
        started, pending, body, event_stream = False, None, bytearray(), False
        path = scope.get("path", "")
        async def emit(message):
            nonlocal started, pending, event_stream
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message["headers"] if k.lower() != b"x-request-id"]
                message["headers"].append((b"x-request-id", rid.encode()))
                content_type = dict(message["headers"]).get(b"content-type", b"")
                event_stream = b"text/event-stream" in content_type
                if b"application/json" in content_type:
                    pending = message
                    return
                started = True
            elif message["type"] == "http.response.body" and pending:
                body.extend(message.get("body", b""))
                if message.get("more_body"):
                    return
                data = json.loads(body) if body else {}
                status = pending["status"]
                error = None
                if status >= 400:
                    existing = data.get("error") if isinstance(data, dict) else None
                    if isinstance(existing, dict) and existing.get("code"):
                        data = {"error": {**existing, "requestId": rid}}
                    else:
                        error = http_error(status, data.get("detail", "Yêu cầu không hợp lệ") if isinstance(data, dict) else "Yêu cầu không hợp lệ")
                        if status == 422 and "/config" in path:
                            error.code = "CONFIG_INVALID"
                elif isinstance(data, dict) and isinstance(data.get("error"), str) and data["error"]:
                    error = runtime.last_error.get() or IntegrationError("MODEL_ERROR", data["error"], 502)
                if error:
                    status, data = error.status, envelope(error, rid)
                encoded = json.dumps(data, ensure_ascii=False).encode()
                pending["status"] = status
                pending["headers"] = [(k, v) for k, v in pending["headers"] if k.lower() != b"content-length"]
                pending["headers"].append((b"content-length", str(len(encoded)).encode()))
                await send(pending)
                started, pending = True, None
                await send({"type": "http.response.body", "body": encoded})
                return
            elif message["type"] == "http.response.body" and b"event: error\n" in message.get("body", b""):
                # Giữ giao thức SSE; không đổi status sau khi stream đã bắt đầu.
                message = {**message, "body": ("event: error\ndata: " + json.dumps(
                    envelope(IntegrationError("MODEL_ERROR", "Không hoàn tất được xử lý; kiểm tra log theo requestId", 502), rid),
                    ensure_ascii=False) + "\n\n").encode()}
            await send(message)
        try:
            try:
                if uuid.UUID(rid).version != 4:
                    raise ValueError()
            except ValueError:
                rid = str(uuid.uuid4())
                runtime.request_id.set(rid)
                raise IntegrationError("INVALID_REQUEST", "X-Request-Id phải là UUID v4", 400)
            if path.startswith("/api/admin/"):
                expected = get_settings().admin_api_key
                if not expected or not secrets.compare_digest(headers.get("x-api-key", "").encode(), expected.encode()):
                    raise IntegrationError("UNAUTHORIZED", "Thiếu hoặc sai X-Api-Key", 401)
            elif path.startswith("/api/"):
                expected = get_settings().public_access_token
                if not expected:
                    raise IntegrationError("CONFIG_INVALID", "Chưa cấu hình token cho API nghiệp vụ", 503)
                supplied_token = headers.get("x-access-token", "")
                if not secrets.compare_digest(supplied_token.encode(), expected.encode()):
                    raise IntegrationError("UNAUTHORIZED", "Thiếu hoặc sai X-Access-Token", 401)
            # Đọc JSON để giữ bộ lọc cho mọi nhánh con của request; multipart không bị đụng tới.
            replay = receive
            if "application/json" in headers.get("content-type", ""):
                chunks = []
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunks.append(message.get("body", b""))
                    if not message.get("more_body"):
                        break
                raw = b"".join(chunks)
                try:
                    data = json.loads(raw)
                    filters = data.get("filters") if isinstance(data, dict) else None
                    filter_token = runtime.metadata_filters.set(filters if isinstance(filters, dict) else None)
                except ValueError:
                    pass  # FastAPI sẽ trả lỗi validation có cấu trúc.
                delivered = False
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": raw, "more_body": False}
                    return await receive()
            code = route_agent(path)
            async with activate(load(code) if code else None):
                await self.app(scope, replay, emit)
        except Exception as exc:
            error = classify(exc)
            logging.getLogger(__name__).warning("requestId=%s code=%s path=%s", rid, error.code, path)
            if not started:
                await JSONResponse(envelope(error, rid), status_code=error.status,
                    headers={"X-Request-Id": rid})(scope, receive, send)
            elif event_stream:
                # Gửi event lỗi khi generator stream phát sinh ngoại lệ.
                await send({"type": "http.response.body", "body": ("event: error\ndata: " +
                    json.dumps(envelope(error, rid), ensure_ascii=False) + "\n\n").encode(), "more_body": False})
        finally:
            if filter_token is not None:
                runtime.metadata_filters.reset(filter_token)
            runtime.request_id.reset(token)
            runtime.last_error.reset(error_token)
