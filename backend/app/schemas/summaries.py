"""Tham số và kết quả API tóm tắt văn bản độc lập UC31."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ReferenceModel


class SummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1, max_length=1000)
    length: Literal["short", "medium", "long"] = "medium"
    structure: Literal["paragraphs", "bullets", "sections"] = "bullets"
    focus: str = Field(default="", max_length=1000)


class SummarySection(BaseModel):
    heading: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=8000)


class SummaryContent(BaseModel):
    sections: list[SummarySection] = Field(min_length=1, max_length=15)


class SummaryResponse(BaseModel):
    ai_task_id: int | None = None
    output_file_url: str = ""
    summary: str = ""
    sections: list[SummarySection] = Field(default_factory=list)
    summary_refs: list[ReferenceModel] = Field(default_factory=list)
    length: str = "medium"
    structure: str = "bullets"
    word_count: int = 0
    coverage: dict = Field(default_factory=lambda: {"complete": False})
    warnings: list[str] = Field(default_factory=list)
    error: str = ""
