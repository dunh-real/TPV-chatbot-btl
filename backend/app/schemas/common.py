"""Schema dùng chung cho toàn bộ API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReferenceModel(BaseModel):
    """Nguồn của một ý do LLM viết ra - UI bấm vào [n] là hiện ngay `snippet`.

    `snippet` đi kèm sẵn chứ không bắt UI gọi ngược lại backend: lúc đó ngữ cảnh
    (chunk nào, khối nào, kỳ nào) đã mất rồi.
    """

    id: int = Field(description="Số hiện trong câu chữ: [1]")
    kind: str = Field(description="block | quy_dinh | du_lieu | cong_cu")
    label: str = ""
    snippet: str = Field(default="", description="Nguyên văn đoạn nguồn")
    locator: dict[str, Any] = Field(
        default_factory=dict, description="Đủ để mở lại đúng chỗ trong nguồn gốc"
    )
    score: float | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str = "error"


class HealthResponse(BaseModel):
    status: str = "ok"
    qdrant: bool = False
    llm: bool = False
    database: bool = False
    collection: str = ""
    points: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class RequestCheckResponse(BaseModel):
    """Server ĐÃ NHẬN ĐƯỢC GÌ - để bên gọi tự chẩn đoán thay vì đoán mò.

    `/health` trả lời "backend còn sống không". Câu hỏi này khác hẳn: "request
    của TÔI tới nơi ở dạng nào". Trình duyệt bị CORS chặn chỉ báo "Failed to
    fetch", không mã lỗi, không nội dung - nên người viết giao diện không phân
    biệt được server chết, sai địa chỉ, hay bị chặn origin.
    """

    nhan_duoc: bool = True
    origin: str | None = Field(default=None, description="Header Origin server nhận được")
    cors_cho_phep: bool | None = Field(
        default=None, description="null = request không kèm Origin (không phải gọi từ trình duyệt)")
    host: str = ""
    scheme: str = Field(default="", description="http | https, theo X-Forwarded-Proto nếu qua proxy")
    qua_proxy: bool = False
    client_ip: str = ""
    method: str = ""
    danh_tinh: dict[str, Any] = Field(default_factory=dict)
    chuan_doan: list[str] = Field(default_factory=list)
    server_time: str = ""
    # Chỉ điền khi gọi từ chính máy chủ - xem ghi chú ở `app.main`.
    cors_da_khai: list[str] | None = None
