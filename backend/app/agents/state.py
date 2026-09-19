"""State dùng chung cho các workflow LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict

from app.rag.retrieval import RetrievalResult, RetrievedChunk


class Citation(TypedDict):
    """Một nguồn được đánh số trong câu trả lời."""

    id: int
    doc_id: str
    doc_title: str
    section: str
    source: str
    page: int | None
    snippet: str
    score: float
    matched_by: str


class QAState(TypedDict, total=False):
    """Trạng thái của workflow 1: hỏi đáp / tra cứu (RAG)."""

    # --- đầu vào ---
    question: str
    conversation_id: str
    history: list[dict[str, str]]
    doc_ids: list[str]
    sources: list[str]
    doc_types: list[str]
    top_n: int
    use_rerank: bool

    # --- trung gian ---
    standalone_query: str
    query_variants: list[str]
    retrieval: RetrievalResult
    chunks: list[RetrievedChunk]
    context: str
    citations: list[Citation]

    # --- đầu ra ---
    answer: str
    used_citations: list[Citation]
    trace: dict[str, Any]
    error: str


class DocumentState(TypedDict, total=False):
    """Trạng thái của workflow 2: xử lý văn bản tự động."""

    # --- đầu vào ---
    file_path: str
    file_name: str
    document_type: str
    noi_gui: str
    departments: list[dict[str, str]]     # danh mục lấy từ CSDL, không để LLM tự nghĩ
    rule_set: str                         # tên file trong config/rules, rỗng = mặc định
    force_rules: bool                     # soát cả khi tệp không giống văn bản hành chính

    # --- trung gian ---
    structure: Any                        # app.documents.parser.DocumentStructure
    components: Any                       # app.documents.structure.DocumentComponents
    rule_result: Any                      # app.documents.rules.RuleCheckResult
    llm_findings: list[dict[str, Any]]
    classification: dict[str, Any] | None
    summary: str
    summary_refs: list[dict[str, Any]]      # nguồn cho từng ý trong tóm tắt
    all_refs: list[dict[str, Any]]          # toàn bộ nguồn, dãy số dùng chung
    deadline: str | None
    tasks: list[dict[str, Any]]

    # --- đầu ra ---
    result: dict[str, Any]
    trace: dict[str, Any]
    error: str


class DraftState(TypedDict, total=False):
    """Trạng thái của workflow 3: soạn văn bản theo mẫu."""

    # --- đầu vào ---
    request: str
    ma_don_vi: str
    history: list[dict[str, str]]   # để hiểu "vẫn đơn vị đó", "cũng kỳ đó"
    inputs: dict[str, Any]          # người ký, chức vụ, số ký hiệu... do người dùng nhập

    # --- trung gian ---
    params: dict[str, Any]
    template: dict[str, Any]
    template_choice: dict[str, Any]
    data: dict[str, Any]            # số liệu từ SQL - nguồn sự thật duy nhất
    data_notes: list[str]
    regulations: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    validation: dict[str, Any]
    retry_count: int

    # --- đầu ra ---
    output_path: str
    so_ky_hieu: str                 # số cấp từ sổ văn bản lúc xuất file
    registered_as: str
    assumptions: list[str]
    missing_input: list[str]
    error: str


class AggregateState(TypedDict, total=False):
    """Trạng thái của workflow 4: tổng hợp báo cáo."""

    # --- đầu vào ---
    request: str
    history: list[dict[str, str]]
    inputs: dict[str, Any]

    # --- trung gian ---
    params: dict[str, Any]
    data: dict[str, Any]              # kết quả các data tool - nguồn số duy nhất
    reconciliation: list[dict[str, Any]]
    has_discrepancy: bool
    charts: dict[str, bytes]          # PNG do code vẽ
    sections: list[dict[str, Any]]
    validation: dict[str, Any]

    # --- đầu ra ---
    output_path: str
    so_ky_hieu: str
    registered_as: str
    assumptions: list[str]
    error: str


class StepResult(TypedDict, total=False):
    """Kết quả một bước của kế hoạch - đủ để dựng lại câu trả lời và truy vết."""

    id: str
    intent: str                       # nghiệp vụ đã chạy (khác `planned_intent` nếu đã lùi)
    planned_intent: str               # nghiệp vụ kế hoạch định chạy
    request: str                      # yêu cầu con của riêng bước này
    answer: str
    result: dict[str, Any]
    citations: list[Citation]
    refs: list[dict[str, Any]]
    missing_input: list[str]
    attempts: int                     # số lần đã chạy, kể cả lần thử lại
    retried_as: str                   # nghiệp vụ dùng cho lần thử lại, rỗng nếu không thử lại
    empty: bool                       # chạy xong mà không ra kết quả dùng được
    error: str


class AgentState(TypedDict, total=False):
    """Trạng thái của agent tổng: lập kế hoạch rồi chạy từng bước.

    Giữ đầu vào của cả năm workflow trong một state duy nhất vì người dùng chỉ gõ
    một câu - hệ thống phải tự quyết định câu đó cần chạy những gì.
    """

    # --- đầu vào ---
    request: str
    conversation_id: str
    history: list[dict[str, str]]
    file_id: str                      # có file đính kèm thì mới đi được nhánh document
    ma_don_vi: str
    inputs: dict[str, Any]            # người ký, số ký hiệu... cho nhánh soạn văn bản

    # --- kế hoạch ---
    plan: dict[str, Any]              # các bước đã qua kiểm tra, kèm lý do tách
    steps: list[StepResult]           # kết quả từng bước, theo thứ tự chạy xong
    intent: str                       # nghiệp vụ của bước chính - giữ cho hợp đồng cũ
    routing: dict[str, Any]

    # --- đầu ra ---
    answer: str                       # câu trả lời cho người dùng
    citations: list[Citation]         # chỉ nhánh hỏi đáp mới có
    refs: list[dict[str, Any]]        # nguồn cho câu trả lời của các nhánh còn lại
    result: dict[str, Any]            # kết quả thô của bước chính
    artifacts: list[dict[str, Any]]   # file sinh ra, kèm đường dẫn tải về
    missing_input: list[str]
    trace: dict[str, Any]
    error: str


class PresentationState(TypedDict, total=False):
    """Trạng thái của workflow 5: tạo bộ slide."""

    # --- đầu vào ---
    request: str
    history: list[dict[str, str]]
    inputs: dict[str, Any]

    # --- trung gian (tái dùng phần lấy số liệu của workflow 4) ---
    params: dict[str, Any]
    data: dict[str, Any]
    brief: str                        # bản tóm tắt số liệu gửi cho Presenton
    n_slides: int
    verify_upto: int                  # số slide đầu do model viết - phạm vi soi số
    validation: dict[str, Any]

    # --- đầu ra ---
    output_path: str
    engine: str                       # presenton | local (đường lùi tự dựng)
    presentation_id: str
    edit_url: str
    elapsed_seconds: float
    slide_count: int
    assumptions: list[str]
    error: str
