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
    # Người dùng tích tài liệu trong panel -> chỉ tra trong đúng những tài liệu đó.
    # Nhận DANH SÁCH ngay từ đầu dù giao diện hiện chỉ cho chọn một: Qdrant lọc
    # bằng `MatchAny`, nên cho chọn nhiều là việc của giao diện, backend không đổi.
    doc_ids: list[str] = Field(
        default_factory=list,
        description="Giới hạn tra cứu trong các doc_id này; bỏ trống = toàn kho",
    )
    sources: list[str] = Field(
        default_factory=list,
        description="Giới hạn theo TÊN FILE, dùng khi phía gọi không có doc_id",
    )
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


class PlanStepModel(BaseModel):
    """Một bước trong kế hoạch, trước khi chạy."""

    id: str = ""
    intent: str = Field(default="", description="qa | document | draft | report | presentation | agent")
    request: str = Field(default="", description="Yêu cầu con, tự đứng một mình được")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Id các bước phải xong trước; rỗng nghĩa là chạy được song song",
    )
    reason: str = ""


class PlanModel(BaseModel):
    steps: list[PlanStepModel] = Field(default_factory=list)
    confidence: float = 0.0
    clarify: str = ""
    reason: str = ""
    source: str = Field(default="", description="llm | router | rule")


class StepResultModel(BaseModel):
    """Một bước sau khi chạy - đủ để giao diện hiện tiến trình và lý do thử lại."""

    id: str = ""
    intent: str = Field(default="", description="Nghiệp vụ đã thật sự chạy")
    planned_intent: str = Field(default="", description="Nghiệp vụ kế hoạch định chạy")
    request: str = ""
    answer: str = ""
    attempts: int = 1
    retried_as: str = Field(
        default="", description="Nghiệp vụ dùng cho lần thử lại; rỗng nếu chạy một lần là xong"
    )
    empty: bool = Field(default=False, description="Chạy xong nhưng không ra kết quả dùng được")
    error: str = ""


class AgentResponse(BaseModel):
    answer: str
    conversation_id: str
    intent: str = Field(default="", description="Nghiệp vụ của bước chính (bước cuối)")
    routing: RoutingInfo = Field(default_factory=RoutingInfo)
    plan: PlanModel = Field(
        default_factory=PlanModel, description="Kế hoạch agent đã lập cho yêu cầu này"
    )
    steps: list[StepResultModel] = Field(
        default_factory=list, description="Kết quả từng bước, theo thứ tự của kế hoạch"
    )
    citations: list[CitationModel] = Field(
        default_factory=list, description="Nguồn trích dẫn của nhánh hỏi đáp"
    )
    refs: list[ReferenceModel] = Field(
        default_factory=list,
        description="Nguồn trích dẫn của các nhánh còn lại; khớp với marker [n] trong answer",
    )
    artifacts: list[Artifact] = Field(default_factory=list)
    session_files: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tài liệu đã tải lên trong hội thoại này, mới nhất trước. "
                    "Người dùng nói \"tài liệu đó\" là trỏ tới phần tử đầu tiên",
    )
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
