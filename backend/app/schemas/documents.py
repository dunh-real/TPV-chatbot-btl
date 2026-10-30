"""Schema cho việc nạp tài liệu vào kho tri thức."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ReferenceModel
from app.schemas.chat import RetrievalFilters


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    limit: int = Field(default=10, ge=1, le=50)
    use_rerank: bool = True


class DocumentSnippet(BaseModel):
    text: str
    section: str = ""
    page: int | None = None


class DocumentDetail(BaseModel):
    doc_id: str
    title: str
    source: str = ""
    doc_type: str = ""
    chunk_count: int = 0
    ingested_at: float | None = None
    original_filename: str = ""
    file_size_bytes: int | None = None
    original_available: bool = False
    detail_url: str
    original_url: str | None = None
    so_hieu: str = ""
    loai: str = ""
    ngay_hieu_luc: str | None = None


class DocumentSearchItem(DocumentDetail):
    score: float
    snippets: list[DocumentSnippet]


class DocumentSearchResponse(BaseModel):
    items: list[DocumentSearchItem]
    returned_count: int
    candidate_documents: int
    score_kind: Literal["rerank", "rrf"]


class DocumentSelection(BaseModel):
    doc_ids: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=200)


class SaveDocumentList(DocumentSelection):
    name: str = Field(min_length=1, max_length=160)


class SavedDocumentList(SaveDocumentList):
    saved_id: str
    created_at: datetime


class SavedDocumentListDetail(SavedDocumentList):
    items: list[DocumentDetail]
    missing_doc_ids: list[str]


class LegalRelatedItem(DocumentDetail):
    relation: str
    direction: Literal["outgoing", "incoming"]


class RelatedDocuments(BaseModel):
    doc_id: str
    same_topic: list[DocumentSearchItem]
    same_number: list[DocumentDetail]
    legal_relations: list[LegalRelatedItem]
    legal_status: Literal["ok", "disabled", "unavailable", "not_registered"]


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
    already_exists: bool = Field(default=False, description="True khi bỏ qua ingest vì file đã có trong kho")


class MinioIngestRequest(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=1024)
    doc_type: str = ""
    force_reingest: bool = False


class CollectionStats(BaseModel):
    collection: str
    points: int
    vectors: list[str] = Field(default_factory=list)


class DocumentItem(BaseModel):
    doc_id: str
    doc_title: str
    source: str = ""
    doc_type: str = ""
    chunk_count: int = 0
    ingested_at: float | None = None
    # VBQPPL (khi bật phiên bản): số hiệu, loại, ngày hiệu lực đọc từ văn bản
    so_hieu: str = ""
    loai: str = ""
    ngay_hieu_luc: str | None = None


class DocumentPage(BaseModel):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


class BatchDeleteRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=200)


class BatchDeleteResponse(BaseModel):
    deleted: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    # Văn bản gốc được dựng lại vì văn bản sửa đổi của nó vừa bị xoá
    rebuilt: list[str] = Field(default_factory=list)


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
    rule_set: str = Field(default="", description="Bộ tiêu chí đã áp, tên file trong config/rules")


class RuleSetInfo(BaseModel):
    """Một bộ tiêu chí có thể chọn khi soát."""

    id: str
    label: str
    version: str = ""


class FindingModel(BaseModel):
    """Một lỗi nội dung, kèm vị trí ký tự để khoanh vùng ngay trên tài liệu."""

    block_id: str
    type: str = Field(
        description='Tất định (`source="rule"`): spacing | punctuation | duplicate. '
                    'LLM (`source="llm"`): spelling | grammar | wording | logic | missing.')
    quote: str = Field(description="Trích nguyên văn, đã xác minh có thật trong văn bản")
    suggest: str = ""
    message: str = ""
    severity: str = "warning"
    start: int | None = Field(default=None, description="Chỉ số ký tự đầu trong text của khối")
    end: int | None = None
    source: str = Field(
        default="llm",
        description='"rule": đối chiếu chuỗi, chắc chắn. "llm": phán đoán, cần người xác nhận.',
    )


# Tên cũ, giữ cho phần `llm_review` không đổi hình dạng với bên đã tích hợp.
LLMFindingModel = FindingModel


class BlockModel(BaseModel):
    """Một khối của tài liệu kèm định dạng thật - đủ để dựng lại trang."""

    id: str
    text: str
    kind: str = "paragraph"
    font: str | None = None
    size_pt: float | None = None
    bold: bool = False
    alignment: str | None = None
    line_spacing: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    style: str | None = None
    page: int | None = None
    bbox: list[float] | None = Field(
        default=None,
        description="Chỉ PDF: [x0, y0, x1, y1] theo điểm in. Dùng để dựng lại "
                    "trang (suy ra canh lề), KHÔNG dùng để kết luận lỗi.",
    )


class GeometryModel(BaseModel):
    """Khổ giấy và lề, quy về mm. `measured` = suy từ vùng chữ chứ không đọc được."""

    width_mm: float
    height_mm: float
    top_mm: float
    bottom_mm: float
    left_mm: float
    right_mm: float
    measured: bool = False


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


class OutlineItemModel(BaseModel):
    """Một mục trong dàn ý dò được, dùng chung cho mọi loại tài liệu."""

    block_id: str
    level: int = 1
    text: str


class DocumentInfoModel(BaseModel):
    source_format: str
    block_count: int = 0
    page_count: int = 0
    has_format_info: bool = False
    title: str = Field(default="", description="Tiêu đề mở đầu tài liệu, rỗng nếu không có")
    outline: list[OutlineItemModel] = Field(default_factory=list)
    default_font: str | None = None
    default_size_pt: float | None = None
    geometry: GeometryModel | None = None


# --------------------------------------------------------------------------- #
# Workflow 2b: soạn văn bản giao việc từ bảng phân công
# --------------------------------------------------------------------------- #
class GiaoViecRequest(BaseModel):
    """Bảng phân công + phần thể thức. Trường rỗng thành dấu chấm lửng trong file.

    `tasks` gửi lại nguyên phần `tasks` của `/review` - có thể đã sửa tay. Backend
    không giữ kết quả soát giữa hai lần gọi, nên nhiệm vụ phải đi kèm yêu cầu.
    """

    loai: Literal["cong_van", "quyet_dinh"] = "cong_van"
    tasks: list[TaskModel] = Field(min_length=1)
    co_quan: str = Field(default="", description="Cơ quan ban hành, in ở góc trái")
    co_quan_chu_quan: str = ""
    so_ky_hieu: str = Field(default="", description="Để trống thì in '...../CV-…' cho người soạn điền")
    dia_danh: str = ""
    ngay: str = Field(default="", description="Để trống thì lấy ngày hôm nay")
    trich_yeu: str = ""
    can_cu: list[str] = Field(default_factory=list, description="Chỉ dùng cho mẫu quyết định")
    nguoi_ky: str = ""
    chuc_vu_ky: str = ""
    deadline: str | None = None
    mo_dau: str = Field(default="", description="Đoạn mở đầu, thường là tóm tắt văn bản đến")


class GiaoViecResponse(BaseModel):
    ai_task_id: int | None = None
    output_file_url: str = ""
    loai: str
    file_name: str
    download_url: str
    task_count: int


class ReviewResponse(BaseModel):
    ai_task_id: int | None = None
    output_file_url: str = ""
    document: DocumentInfoModel
    blocks: list[BlockModel] = Field(
        default_factory=list,
        description="Nội dung + định dạng từng khối, để dựng lại tài liệu và khoanh vùng lỗi",
    )
    blocks_truncated: bool = Field(
        default=False, description="True: tài liệu quá dài nên `blocks` đã bị cắt bớt"
    )
    findings: list[FindingModel] = Field(
        default_factory=list,
        description="Lỗi nội dung, danh sách phẳng theo thứ tự đọc, có vị trí ký tự",
    )
    rule_check: RuleCheckModel
    llm_review: dict[str, list[FindingModel]] = Field(
        default_factory=dict, description="Cùng dữ liệu như `findings`, gom theo mã khối"
    )
    classification: ClassificationModel | None = None
    summary: str = ""
    summary_refs: list[ReferenceModel] = Field(
        default_factory=list, description="Khớp với marker [n] trong summary"
    )
    deadline: str | None = None
    tasks: list[TaskModel] = Field(default_factory=list)
    giao_viec_goi_y: str = Field(
        default="cong_van",
        description="Mẫu văn bản giao việc nên chọn sẵn: cong_van | quyet_dinh",
    )
    totals: dict[str, int] = Field(default_factory=dict)
    error: str = ""
