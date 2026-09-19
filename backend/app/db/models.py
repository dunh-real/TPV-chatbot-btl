"""Dữ liệu do CHÍNH hệ thống này sinh ra và quản lý.

Phòng ban, trang bị, nhân sự không nằm ở đây: chúng thuộc về ERP và chỉ được đọc
(xem `app.db.erp_models`). Còn lại là hai thứ ERP không có chỗ chứa - mẫu báo cáo
và sổ văn bản do hệ thống tự phát sinh - nên vẫn cần một CSDL riêng để ghi.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Unicode, UnicodeText, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TemplateBaoCao(Base):
    """Mẫu báo cáo: mô tả để LLM chọn, và cấu trúc trường để hệ thống điền."""

    __tablename__ = "template_bao_cao"

    ma_template: Mapped[str] = mapped_column(Unicode(50), primary_key=True)
    ten_bao_cao: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    loai_bao_cao: Mapped[str] = mapped_column(Unicode(100), nullable=False, default="")
    mo_ta: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    # File .docx mẫu (đã đúng thể thức) để điền vào; để trống thì dựng bằng code.
    file_path: Mapped[str] = mapped_column(Unicode(1000), nullable=False, default="")
    # JSON mô tả các trường của báo cáo. SQL Server không có kiểu JSON riêng nên
    # lưu NVARCHAR(MAX); đọc ra bằng `fields`.
    truong_du_lieu: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="{}")

    @property
    def fields(self) -> dict[str, Any]:
        try:
            return json.loads(self.truong_du_lieu or "{}")
        except json.JSONDecodeError:
            return {}


class VanBan(Base):
    """Sổ văn bản đến/đi, trỏ tới file gốc đã lưu."""

    __tablename__ = "van_ban"

    ma_van_ban: Mapped[str] = mapped_column(Unicode(100), primary_key=True)
    ten_van_ban: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    loai_van_ban: Mapped[str] = mapped_column(Unicode(100), nullable=False, default="")
    noi_gui: Mapped[str] = mapped_column(Unicode(255), nullable=False, default="")
    noi_nhan: Mapped[str] = mapped_column(Unicode(500), nullable=False, default="")
    # Mã đơn vị và kỳ của báo cáo - hai trường quyết định câu "đơn vị nào đã gửi
    # báo cáo kỳ này". Trước đây phải suy ra bằng cách dò TÊN đơn vị trong
    # `noi_gui` và lấy `ngay_van_ban` làm kỳ; cả hai đều sai: tên đơn vị đổi cách
    # viết là mất dấu, còn báo cáo tháng 8 ký ngày 19/9 thì bị tính sang kỳ 9 -
    # nên mục "tình hình gửi báo cáo" luôn in 0/9 đơn vị dù sổ có bản ghi.
    # Để trống với văn bản đến hoặc bản ghi cũ; khi đó vẫn dò theo cách cũ.
    ma_don_vi: Mapped[str | None] = mapped_column(Unicode(50), nullable=True, index=True)
    ky: Mapped[str | None] = mapped_column(Unicode(7), nullable=True, index=True)
    mo_ta: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    ngay_van_ban: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_path: Mapped[str] = mapped_column(Unicode(1000), nullable=False, default="")
    dang_file: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="")
    # doc_id trong Qdrant - cầu nối sang workflow 1 để tra cứu nội dung.
    doc_id: Mapped[str | None] = mapped_column(Unicode(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
