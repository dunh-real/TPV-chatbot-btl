"""Hình dạng dữ liệu của API sơ đồ tư duy."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MindmapSource(BaseModel):
    """Một tài liệu trong kho, dùng để chọn nguồn dựng sơ đồ."""

    doc_id: str
    doc_title: str
    doc_type: str = ""
    chunk_count: int = 0
    # Đã dựng sơ đồ cho tài liệu này chưa - giao diện hiện dấu để khỏi dựng lại.
    has_mindmap: bool = False


class MindmapNode(BaseModel):
    """Một mục trong cây. Nội dung chi tiết xin riêng qua /section."""

    id: str
    title: str
    has_content: bool = False
    children: list["MindmapNode"] = Field(default_factory=list)


class MindmapSummary(BaseModel):
    doc_id: str
    doc_title: str
    node_count: int
    saved_at: str | None = None


class MindmapResponse(BaseModel):
    doc_id: str
    doc_title: str
    node_count: int
    tree: MindmapNode
    saved_at: str | None = None
    # True = lấy từ bản đã lưu, không tốn lượt LLM nào.
    cached: bool = False


class GenerateRequest(BaseModel):
    doc_id: str = Field(description="doc_id của tài liệu trong kho")
    regenerate: bool = Field(default=False, description="Bỏ bản đã lưu, dựng lại từ đầu")


class SectionRequest(BaseModel):
    node_id: str = Field(description="id của mục cần xem nội dung")


class SectionResponse(BaseModel):
    doc_id: str
    node_id: str
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    cached: bool = False
