"""Mã lỗi tích hợp thống nhất, không đưa thông tin kết nối hoặc traceback ra client."""
import logging
import httpx

CODES = {"UNAUTHORIZED", "INVALID_REQUEST", "MODEL_UNREACHABLE", "MODEL_ERROR",
         "CONTEXT_TOO_LONG", "TIMEOUT", "OVERLOADED", "NO_CONTEXT", "INTERNAL_ERROR",
         "TENANT_MISMATCH", "PARTITION_DENIED", "AGENT_NOT_FOUND", "CONFIG_INVALID"}


class IntegrationError(Exception):
    def __init__(self, code, message, status=400):
        assert code in CODES
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def classify(exc):
    if isinstance(exc, IntegrationError):
        return exc
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return IntegrationError("TIMEOUT", "Quá thời gian xử lý yêu cầu", 504)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 400 and any(word in exc.response.text.lower() for word in ("context length", "context window", "too many tokens")):
            return IntegrationError("CONTEXT_TOO_LONG", "Đầu vào vượt context của mô hình", 413)
        return IntegrationError("OVERLOADED" if status in (429, 503) else "MODEL_ERROR",
                                "Mô hình quá tải" if status in (429, 503) else "Mô hình trả lỗi", 503 if status in (429, 503) else 502)
    if isinstance(exc, httpx.TransportError):
        return IntegrationError("MODEL_UNREACHABLE", "Không kết nối được mô hình", 502)
    if isinstance(exc, PermissionError) or type(exc).__name__ == "ThieuQuyen":
        return IntegrationError("PARTITION_DENIED", "Không có quyền thực hiện hoặc truy cập dữ liệu", 403)
    if type(exc).__name__ == "LLMError":
        if exc.__cause__ is not None:
            return classify(exc.__cause__)
        return IntegrationError("MODEL_ERROR", "Phản hồi mô hình không hợp lệ", 502)
    logging.getLogger(__name__).exception("Lỗi tích hợp", exc_info=exc)
    return IntegrationError("INTERNAL_ERROR", "Dịch vụ gặp sự cố; kiểm tra log theo requestId", 500)


def envelope(error, rid):
    return {"error": {"code": error.code, "message": error.message, "requestId": rid}}
