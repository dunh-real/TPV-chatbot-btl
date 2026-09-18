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
