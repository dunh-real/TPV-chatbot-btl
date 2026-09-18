"""Schema cho workflow 1: hỏi đáp / tra cứu tài liệu."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CitationModel(BaseModel):
    id: int = Field(description="Số trích dẫn xuất hiện trong câu trả lời, ví dụ [1]")
    doc_id: str
    doc_title: str
    section: str = ""
    source: str = ""
    page: int | None = None
    snippet: str = ""
    score: float = Field(0.0, description="Điểm reranker trong [0,1]")


class RetrievalFilters(BaseModel):
    doc_ids: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    doc_types: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None, description="Bỏ trống để hệ thống tạo hội thoại mới"
    )
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_n: int | None = Field(default=None, ge=1, le=20, description="Số chunk đưa vào ngữ cảnh")
    use_rerank: bool = True
    include_trace: bool = Field(default=False, description="Trả kèm thông tin debug pipeline")


class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    citations: list[CitationModel] = Field(default_factory=list)
    standalone_query: str = ""
    query_variants: list[str] = Field(default_factory=list)
    trace: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    """Chỉ chạy phần truy hồi - dùng để kiểm tra chất lượng hybrid + RRF."""

    query: str = Field(min_length=1, max_length=4000)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_n: int = Field(default=10, ge=1, le=50)
    use_rerank: bool = True
    rewrite: bool = False


class SearchHit(BaseModel):
    point_id: str
    text: str
    doc_id: str
    doc_title: str
    section: str = ""
    page: int | None = None
    rrf_score: float
    rerank_score: float
    branch_ranks: dict[str, int] = Field(
        default_factory=dict, description="Hạng của chunk trong từng nhánh: dense / lexical / bm25"
    )


class SearchResponse(BaseModel):
    queries: list[str]
    hits: list[SearchHit]
    branch_hits: dict[str, int] = Field(default_factory=dict)
    fused_count: int = 0
    timings_ms: dict[str, float] = Field(default_factory=dict)


class HistoryTurn(BaseModel):
    role: str
    content: str
    ts: float | None = None


class HistoryResponse(BaseModel):
    conversation_id: str
    turns: list[HistoryTurn] = Field(default_factory=list)
