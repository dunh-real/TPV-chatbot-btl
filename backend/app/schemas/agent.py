"""Schema cho agent tổng: một cửa vào cho cả năm workflow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.chat import CitationModel
from app.schemas.common import ReferenceModel


class AgentRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000,
                         description="Yêu cầu bằng tiếng Việt, không cần nêu workflow")
    conversation_id: str | None = Field(
        default=None, description="Bỏ trống để hệ thống tạo hội thoại mới"
    )
    file_id: str = Field(
        default="", description="Định danh file đã tải lên (POST /api/agent/upload)"
    )
    ma_don_vi: str = Field(default="", description="Đơn vị mặc định khi soạn văn bản")
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Người ký, số ký hiệu, nơi nhận... nếu đã biết"
    )
    include_trace: bool = False


class Artifact(BaseModel):
    file_id: str
    file_name: str
    kind: str = Field(description="docx | pptx | pdf")
    download_url: str = ""


class RoutingInfo(BaseModel):
    intent: str = ""
    confidence: float = 0.0
    reason: str = ""
    clarify: str = ""
    source: str = Field(default="", description="llm | keyword | rule")


class AgentResponse(BaseModel):
    answer: str
    conversation_id: str
    intent: str = ""
    routing: RoutingInfo = Field(default_factory=RoutingInfo)
    citations: list[CitationModel] = Field(
        default_factory=list, description="Nguồn trích dẫn của nhánh hỏi đáp"
    )
    refs: list[ReferenceModel] = Field(
        default_factory=list,
        description="Nguồn trích dẫn của các nhánh còn lại; khớp với marker [n] trong answer",
    )
    artifacts: list[Artifact] = Field(default_factory=list)
    missing_input: list[str] = Field(
        default_factory=list, description="Thiếu thông tin này thì agent dừng để hỏi lại"
    )
    result: dict[str, Any] = Field(
        default_factory=dict, description="Kết quả thô của workflow đã chạy"
    )
    trace: dict[str, Any] | None = None
    error: str = ""


class UploadResponse(BaseModel):
    file_id: str
    name: str
    kind: str
    size: int
    modified: str | None = None


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: dict[str, str] = Field(default_factory=dict)
    reads_database: bool = False
