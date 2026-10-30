"""Schema cho workflow 5: tạo bộ slide."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.reports import ValidationModel


class PresentationRequest(BaseModel):
    nguon: Literal["csdl", "tai_lieu"] = "csdl"
    file_id: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_source(self):
        if self.nguon == "tai_lieu" and not self.file_id.strip():
            raise ValueError("Tạo slide từ tài liệu cần file_id của file đã upload")
        return self

    request: str = Field(min_length=1, max_length=2000,
                         examples=["Tạo slide báo cáo nhân sự tháng 8"])
    inputs: dict[str, str] = Field(
        default_factory=dict, description="Thông tin bổ sung: người trình bày, đơn vị..."
    )


class PresentationResponse(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    document_outline: dict[str, Any] | None = None
    ai_task_id: int | None = None
    output_file_url: str = ""
    request: str
    data: dict[str, Any] = Field(default_factory=dict, description="QueryResult trong data.queries")
    params: dict[str, Any] = Field(default_factory=dict)
    brief: str = Field(
        default="",
        description="Bản tóm tắt số liệu do hệ thống dựng và gửi cho Presenton. "
                    "Đây là toàn bộ thứ bên kia nhìn thấy - đối chiếu ở đây để biết "
                    "một con số lạ trên slide là do số liệu sai hay do bên kia viết thêm.",
    )
    engine: str = Field(
        default="", description="presenton | local (đường lùi hệ thống tự dựng)"
    )
    validation: ValidationModel = Field(default_factory=ValidationModel)
    output_path: str = ""
    download_url: str = ""
    edit_url: str = Field(
        default="", description="Đường dẫn sửa bộ slide ngay trong giao diện Presenton"
    )
    slide_count: int = Field(default=0, description="Đếm từ chính file .pptx")
    elapsed_seconds: float = 0.0
    presentation_id: str = ""
    summary_source: str = Field(
        default="", description="qwen | code - ai viết phần chữ dẫn dắt khi dựng thẳng từ template"
    )
    deck_check: dict[str, Any] = Field(
        default_factory=dict,
        description="Đối chiếu deck Presenton đã lưu với nội dung đã soạn: layout, chữ, biểu đồ",
    )
    timings: dict[str, float] = Field(
        default_factory=dict, description="Thời gian từng công đoạn dựng slide, giây"
    )
    assumptions: list[str] = Field(default_factory=list)
    error: str = ""
