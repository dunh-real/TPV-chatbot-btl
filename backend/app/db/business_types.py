"""Thông tin nghiệp vụ trả cho workflow; không khai báo bảng SQLite."""
from dataclasses import dataclass, field
from datetime import date


@dataclass
class TemplateInfo:
    ma_template: str
    ten_bao_cao: str
    loai_bao_cao: str = ""
    mo_ta: str = ""
    file_path: str = ""
    fields: dict = field(default_factory=dict)
    ai_id: int | None = None
    ai_version: int | None = None
    ai_hash: str | None = None


@dataclass
class DocumentInfo:
    ma_van_ban: str
    ten_van_ban: str
    noi_gui: str = ""
    ngay_van_ban: date | None = None
    doc_id: str | None = None
    loai_van_ban: str = ""
    noi_nhan: str = ""
    file_path: str = ""
