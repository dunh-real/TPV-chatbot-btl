"""Hợp đồng phân tích mẫu và mapping được BE lưu theo phiên bản."""
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

PERIOD_PATTERN = r"^[1-9]\d{3}(?:-(?:0[1-9]|1[0-2]|Q[1-4]))?$"


class TemplateField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    target: str = Field(min_length=1, max_length=400)
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["input", "value", "narrative", "table", "calculation"]
    definition: str = Field(default="", max_length=1500)
    data_type: Literal["text", "integer", "decimal"] = "text"
    unit: str = Field(default="", max_length=40)
    required: bool = True
    replace_existing: bool = Field(default=False, description="BE/người dùng duyệt thay nội dung ví dụ ở đoạn/ô này")
    input_key: str = Field(default="", max_length=64, pattern=r"^[a-zA-Z0-9_]*$")
    text_template: str = Field(default="", max_length=1500, description="Vùng input: văn bản có {{ky}} hoặc {{ky_truoc}} đã duyệt")
    operation: Literal["sum", "difference", "ratio_percent"] | None = None
    operands: list[str] = Field(default_factory=list, max_length=20)
    columns: list[str] = Field(default_factory=list, max_length=12)
    @model_validator(mode="after")
    def valid(self):
        if self.text_template and (self.kind != 'input' or self.input_key not in {'ky','ky_truoc'}):
            raise ValueError("Văn bản kỳ động cần vùng input với input_key=ky hoặc ky_truoc")
        tokens = re.findall(r'\{\{(.*?)\}\}', self.text_template)
        if any(token not in {'ky','ky_truoc'} for token in tokens):
            raise ValueError("Văn bản kỳ động chỉ nhận {{ky}} và {{ky_truoc}}")
        if self.kind == "calculation" and (not self.operation or not self.operands):
            raise ValueError("Trường tính toán cần operation và operands")
        if self.operation in {"difference", "ratio_percent"} and len(self.operands) != 2:
            raise ValueError("Hiệu/tỷ lệ cần đúng hai toán hạng")
        if self.kind == "table" and not self.columns:
            raise ValueError("Bảng cần khai báo nhãn cột")
        return self


class TemplateMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    file_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: list[TemplateField] = Field(min_length=1, max_length=30)
    @model_validator(mode="after")
    def unique(self):
        if len({f.id for f in self.fields}) != len(self.fields) or len({f.target for f in self.fields}) != len(self.fields):
            raise ValueError("ID và vị trí trường không được trùng")
        ids = {f.id for f in self.fields if f.kind == "value"}
        if any(set(f.operands) - ids for f in self.fields if f.kind == "calculation"):
            raise ValueError("Toán hạng chỉ được tham chiếu trường value, không tạo vòng lặp")
        return self


class TemplateAnalyzeRequest(BaseModel):
    file_id: str = Field(min_length=1, max_length=1000)


class TemplateConfirmRequest(TemplateAnalyzeRequest):
    mapping: TemplateMapping


class TemplatePreviewRequest(TemplateConfirmRequest):
    period: str = Field(default="", pattern=PERIOD_PATTERN + r"|^$", description="YYYY, YYYY-MM hoặc YYYY-Q1..Q4")
    content: str = Field(default="", max_length=20000)
    request: str = Field(min_length=1, max_length=2000)
    nguon: Literal["csdl", "tai_lieu", "kho_tri_thuc"] = "tai_lieu"
    source_file_id: str = Field(default="", max_length=1000)
    doc_ids: list[str] = Field(default_factory=list, max_length=30)
    inputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_required(self):
        if any(f.kind in {"value", "narrative", "table"} for f in self.mapping.fields):
            if self.nguon == "tai_lieu" and not self.source_file_id:
                raise ValueError("Cần chọn tài liệu nguồn")
            if self.nguon == "kho_tri_thuc" and not self.doc_ids:
                raise ValueError("Cần chọn mã tài liệu kho")
        return self
