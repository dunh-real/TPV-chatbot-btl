"""Hình dạng dữ liệu của API OCR tài liệu."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OcrStatus(BaseModel):
    """Cấu hình OCR đang chạy - giao diện hiện tên model để biết ai đang đọc."""

    enabled: bool
    model: str = ""
    base_url: str = ""
    max_concurrency: int = 0
    render_scale: float = 0.0
    dinh_dang: list[str] = Field(default_factory=list, description="Đuôi file nhận được")


class OcrTrang(BaseModel):
    so_trang: int = Field(description="Đánh số từ 1")
    nguon: Literal["ocr", "digital", "trong", "loi"] = Field(
        description="ocr = mô hình đọc ảnh · digital = lấy thẳng lớp text · "
                    "trong = trang không có chữ · loi = phải OCR nhưng đọc hỏng"
    )
    so_ky_tu: int = 0
    markdown: str = ""


class OcrRequest(BaseModel):
    file_id: str = Field(description="Định danh file từ POST /api/agent/upload")
    che_do: Literal["auto", "tat_ca"] = Field(
        default="auto",
        description="auto = chỉ OCR trang scan · tat_ca = ép mọi trang qua mô hình",
    )


class OcrResponse(BaseModel):
    file_name: str
    che_do: str
    model: str
    so_trang: int
    so_trang_ocr: int = Field(description="Số trang do mô hình đọc")
    so_trang_digital: int = Field(description="Số trang lấy thẳng từ lớp text")
    so_trang_loi: int = Field(default=0, description="Số trang phải OCR nhưng đọc hỏng")
    giay: float = Field(default=0.0, description="Thời gian chạy, tính bằng giây")
    markdown: str = Field(description="Toàn bộ tài liệu, các trang ngăn bằng ---")
    trang: list[OcrTrang] = Field(default_factory=list)
