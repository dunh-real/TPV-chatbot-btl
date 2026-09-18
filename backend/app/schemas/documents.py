"""Schema cho việc nạp tài liệu vào kho tri thức."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ReferenceModel


class IngestTextRequest(BaseModel):
    text: str = Field(min_length=1)
    doc_title: str = Field(min_length=1, max_length=300)
    doc_id: str | None = None
    source: str = ""
    doc_type: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    doc_id: str
    doc_title: str
    chunk_count: int
    elapsed_ms: float
    source: str = ""


class CollectionStats(BaseModel):
    collection: str
    points: int
    vectors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Workflow 2: xử lý văn bản
# --------------------------------------------------------------------------- #
class RuleFindingModel(BaseModel):
    rule: str
    severity: str
    message: str
    block_id: str | None = None
    actual: str | None = None
    expected: str | None = None
    quote: str = ""


class RuleCheckModel(BaseModel):
    status: str = Field(description="done | partial | skipped")
    findings: list[RuleFindingModel] = Field(default_factory=list)
    passed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    reason: str = ""


class LLMFindingModel(BaseModel):
    block_id: str
    type: str = Field(description="spelling | grammar | wording | logic | missing")
    quote: str = Field(description="Trích nguyên văn, đã xác minh có thật trong văn bản")
    suggest: str = ""
    message: str = ""
    severity: str = "warning"


class ClassificationModel(BaseModel):
    """Chỉ còn loại và chủ đề: việc phòng ban nào làm gì đã dồn hết sang `tasks`."""

    document_type: str
    topic: str = ""
    confidence: float = 0.0
    reason: str = ""


class TaskModel(BaseModel):
    department: str = Field(
        default="", description="Mã phòng ban; rỗng nghĩa là nơi nhận ngoài danh mục"
    )
    department_name: str = ""
    in_catalog: bool = Field(
        default=True, description="False: không có mã/email để chuyển tự động"
    )
    task: str
    refs: list[ReferenceModel] = Field(
        default_factory=list, description="Khối văn bản sinh ra nhiệm vụ này"
    )
    data_needed: list[str] = Field(default_factory=list)
    deadline: str | None = None


class DocumentInfoModel(BaseModel):
    source_format: str
    block_count: int = 0
    page_count: int = 0
    has_format_info: bool = False
    components: list[str] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    document: DocumentInfoModel
    rule_check: RuleCheckModel
    llm_review: dict[str, list[LLMFindingModel]] = Field(default_factory=dict)
    classification: ClassificationModel | None = None
    summary: str = ""
    summary_refs: list[ReferenceModel] = Field(
        default_factory=list, description="Khớp với marker [n] trong summary"
    )
    deadline: str | None = None
    tasks: list[TaskModel] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    error: str = ""
