"""Schema cho workflow 5: tạo bộ slide."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.reports import ValidationModel


class PresentationRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000,
                         examples=["Tạo slide báo cáo quân số tháng 8"])
    inputs: dict[str, str] = Field(
        default_factory=dict, description="Thông tin bổ sung: người trình bày, đơn vị..."
    )


class PresentationResponse(BaseModel):
    request: str
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
    assumptions: list[str] = Field(default_factory=list)
    error: str = ""
