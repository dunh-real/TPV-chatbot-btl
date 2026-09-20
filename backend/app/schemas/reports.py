"""Schema cho workflow 3: soạn văn bản theo mẫu."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ReferenceModel


class DraftRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000,
                         examples=["Soạn báo cáo tình hình trang bị tháng 8"])
    ma_don_vi: str | None = Field(
        default=None, description="Đơn vị lập báo cáo; bỏ trống thì hệ thống sẽ hỏi lại"
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description="Các trường người dùng phải nhập: nguoi_ky, chuc_vu_ky, so_ky_hieu...",
        examples=[{"nguoi_ky": "Trần Văn B", "chuc_vu_ky": "TRƯỞNG ĐƠN VỊ"}],
    )
    nguon: Literal["csdl", "tai_lieu"] = Field(
        default="csdl",
        description="csdl = lấy số liệu ERP theo mẫu; tai_lieu = soạn từ một file đã tải lên",
    )
    file_id: str = Field(
        default="",
        description="Bắt buộc khi nguon=tai_lieu. Lấy từ POST /api/agent/upload",
        examples=["upload:bao_cao_don_vi.docx"],
    )


class SectionModel(BaseModel):
    id: str
    title: str
    kind: str = Field(description="data | table | llm")
    paragraphs: list[str] = Field(
        default_factory=list, description="Bản sạch - đúng thứ đã đổ vào file"
    )
    llm_written_cited: list[str] = Field(
        default_factory=list,
        description="Cùng nội dung nhưng còn marker [n], để UI dựng chỗ bấm",
    )
    refs: list[ReferenceModel] = Field(default_factory=list)
    table: dict[str, Any] | None = None


class ValidationIssue(BaseModel):
    type: str = Field(description="unverified_number | empty_section")
    section: str
    severity: str
    numbers: list[str] = Field(default_factory=list)
    quote: str = ""


class ValidationModel(BaseModel):
    # Có mặc định vì workflow dừng sớm (thiếu đầu vào, lỗi tra cứu) trả về
    # `validation: {}` - chưa chạy bước đối chiếu nào thì đúng là "skipped".
    # Để trường này bắt buộc thì chính đường "thiếu đầu vào" lại nổ ra HTTP 500,
    # và người dùng không bao giờ đọc được câu nhắc bổ sung thông tin.
    status: str = Field(default="skipped", description="passed | warning | failed | skipped")
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_numbers: int = 0


class RegulationRef(BaseModel):
    doc_title: str
    section: str = ""
    text: str = ""
    score: float = 0.0


class DraftResponse(BaseModel):
    nguon: str = Field(default="csdl", description="Nguồn số liệu đã dùng")
    source_document: dict[str, Any] = Field(
        default_factory=dict,
        description="Chỉ nhánh tài liệu: file nào, có đọc được bảng số liệu không",
    )
    request: str
    params: dict[str, Any] = Field(default_factory=dict)
    ma_don_vi: str = ""
    template: str = ""
    template_name: str = ""
    sections: list[SectionModel] = Field(default_factory=list)
    validation: ValidationModel = Field(default_factory=ValidationModel)
    output_path: str = ""
    download_url: str = ""
    registered_as: str = ""
    regulations: list[RegulationRef] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Giả định hệ thống tự đưa ra, ví dụ suy năm hiện tại"
    )
    data_notes: list[str] = Field(
        default_factory=list, description="Ghi chú về nguồn số liệu, ví dụ lùi về kỳ gần nhất"
    )
    missing_input: list[str] = Field(
        default_factory=list, description="Thông tin còn thiếu, cần người dùng bổ sung"
    )
    retry_count: int = 0
    error: str = ""


class TemplateSummary(BaseModel):
    ma_template: str
    ten_bao_cao: str
    loai_bao_cao: str = ""
    mo_ta: str = ""


# --------------------------------------------------------------------------- #
# Workflow 4: tổng hợp báo cáo
# --------------------------------------------------------------------------- #
class AggregateRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000,
                         examples=["Tổng hợp báo cáo quân số và trang thiết bị tháng 8"])
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description="nguoi_ky, chuc_vu_ky, so_ky_hieu, noi_nhan... nếu muốn ghi đè mặc định",
    )


class DiscrepancyModel(BaseModel):
    field: str
    label: str
    db_value: Any = None
    file_value: Any = None
    message: str = ""


class ReconcileModel(BaseModel):
    ma_don_vi: str
    file_path: str = ""
    status: str = Field(description="matched | mismatched | unreadable | no_file")
    extracted: dict[str, int] = Field(default_factory=dict)
    discrepancies: list[DiscrepancyModel] = Field(default_factory=list)


class AggregateSection(BaseModel):
    id: str
    title: str
    paragraphs: list[str] = Field(default_factory=list)
    table: dict[str, Any] | None = None
    has_chart: bool = False
    image_caption: str = ""


class AggregateResponse(BaseModel):
    request: str
    params: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Kết quả thô của các data tool - nguồn của mọi con số trong báo cáo",
    )
    sections: list[AggregateSection] = Field(default_factory=list)
    reconciliation: list[ReconcileModel] = Field(default_factory=list)
    has_discrepancy: bool = False
    validation: ValidationModel = Field(default_factory=ValidationModel)
    output_path: str = ""
    download_url: str = ""
    registered_as: str = ""
    assumptions: list[str] = Field(default_factory=list)
    error: str = ""
