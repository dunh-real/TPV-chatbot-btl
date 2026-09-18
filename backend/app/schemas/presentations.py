"""Schema cho workflow 5: tạo bộ slide."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ReferenceModel

from app.schemas.reports import ValidationModel


class PresentationRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000,
                         examples=["Tạo slide báo cáo quân số tháng 8"])
    inputs: dict[str, str] = Field(
        default_factory=dict, description="Thông tin bổ sung: người trình bày, đơn vị..."
    )


class MetricBoxModel(BaseModel):
    label: str
    value: str
    note: str = ""


class SlideModel(BaseModel):
    kind: str = Field(description="title | summary | bullet | chart | table")
    title: str = ""
    subtitle: str = ""
    focus: str = ""
    bullets: list[str] = Field(
        default_factory=list, description="Bản sạch - đúng thứ đã lên slide"
    )
    bullets_cited: list[str] = Field(
        default_factory=list, description="Cùng nội dung nhưng còn marker [n]"
    )
    refs: list[ReferenceModel] = Field(default_factory=list)
    metrics: list[MetricBoxModel] = Field(default_factory=list)
    chart_key: str | None = None
    data_key: str | None = None
    notes: str = ""


class PresentationResponse(BaseModel):
    request: str
    params: dict[str, Any] = Field(default_factory=dict)
    outline: dict[str, Any] = Field(
        default_factory=dict, description="JSON trung gian do LLM sinh, đã lọc"
    )
    slides: list[SlideModel] = Field(default_factory=list)
    validation: ValidationModel = Field(default_factory=ValidationModel)
    output_path: str = ""
    download_url: str = ""
    slide_count: int = 0
    removed_bullets: int = Field(
        default=0, description="Số gạch đầu dòng bị loại vì có con số không truy được"
    )
    assumptions: list[str] = Field(default_factory=list)
    error: str = ""
