"""Mô hình dữ liệu nghiệp vụ dùng chung cho các workflow.

Viết bằng SQLAlchemy 2.0 nên cùng một bộ model chạy được trên SQL Server (pyodbc)
lẫn SQLite - chỉ khác chuỗi kết nối. Kiểu chuỗi dùng `Unicode`/`UnicodeText` để
SQL Server sinh ra NVARCHAR, giữ đúng tiếng Việt có dấu.

Bảng "tài nguyên đơn vị" được tách làm hai: quân số thuộc về đơn vị, trang bị là
nhiều dòng của đơn vị đó. Gộp chung một bảng thì mỗi lần đơn vị có thêm trang bị
là quân số bị lặp lại, và chỉ cần sót một dòng khi cập nhật là báo cáo ra số sai.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    UniqueConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Unicode,
    UnicodeText,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PhongBan(Base):
    """Danh mục phòng ban - nguồn cho việc đề xuất đơn vị xử lý văn bản."""

    __tablename__ = "phong_ban"

    ma_phong_ban: Mapped[str] = mapped_column(Unicode(50), primary_key=True)
    ten_phong_ban: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    email: Mapped[str] = mapped_column(Unicode(255), nullable=False, default="")
    # Mô tả chức năng/lĩnh vực phụ trách. Đây là trường quyết định chất lượng
    # định tuyến: chỉ có mã và tên phòng thì LLM không suy ra được gì.
    mo_ta: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")

    def as_catalog_entry(self) -> dict[str, str]:
        return {
            "ma_phong_ban": self.ma_phong_ban,
            "ten_phong_ban": self.ten_phong_ban,
            "mo_ta": self.mo_ta,
        }


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


class DonVi(Base):
    """Đơn vị và quân số của đơn vị đó."""

    __tablename__ = "don_vi"

    ma_don_vi: Mapped[str] = mapped_column(Unicode(50), primary_key=True)
    ten_don_vi: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    quan_so: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quan_so_kiem_ke: Mapped[date | None] = mapped_column(Date, nullable=True)

    trang_bi: Mapped[list["TrangBi"]] = relationship(
        back_populates="don_vi", cascade="all, delete-orphan", lazy="selectin"
    )


class TrangBi(Base):
    """Một loại trang bị thuộc một đơn vị."""

    __tablename__ = "trang_bi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ma_don_vi: Mapped[str] = mapped_column(
        Unicode(50), ForeignKey("don_vi.ma_don_vi"), nullable=False, index=True
    )
    ten_trang_bi: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    so_luong: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tinh_trang: Mapped[str] = mapped_column(Unicode(100), nullable=False, default="")
    bao_duong_cuoi: Mapped[date | None] = mapped_column(Date, nullable=True)

    don_vi: Mapped[DonVi] = relationship(back_populates="trang_bi")


class KyKiemKe(Base):
    """Một lần kiểm kê của đơn vị trong một kỳ.

    `don_vi`/`trang_bi` giữ hiện trạng; bảng này giữ lịch sử theo kỳ để trả lời
    được "báo cáo tháng 8" - không có nó thì mọi tháng ra cùng một con số.
    """

    __tablename__ = "kiem_ke"
    __table_args__ = (UniqueConstraint("ma_don_vi", "ky", name="uq_kiem_ke_don_vi_ky"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ma_don_vi: Mapped[str] = mapped_column(
        Unicode(50), ForeignKey("don_vi.ma_don_vi"), nullable=False, index=True
    )
    ky: Mapped[str] = mapped_column(Unicode(7), nullable=False, index=True)   # "2026-08"
    ngay_kiem_ke: Mapped[date | None] = mapped_column(Date, nullable=True)
    quan_so: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Chi tiết quân số. Ràng buộc nghiệp vụ: co_mat + vang = quan_so,
    # di_hoc + nghi_phep <= vang. Lệch thì phải báo, không được để LLM làm mượt.
    co_mat: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vang: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    di_hoc: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nghi_phep: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ghi_chu: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")

    trang_bi: Mapped[list["KiemKeTrangBi"]] = relationship(
        back_populates="kiem_ke", cascade="all, delete-orphan", lazy="selectin"
    )


class KiemKeTrangBi(Base):
    """Trang bị ghi nhận tại một kỳ kiểm kê."""

    __tablename__ = "kiem_ke_trang_bi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kiem_ke_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kiem_ke.id"), nullable=False, index=True
    )
    ten_trang_bi: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    so_luong: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tinh_trang: Mapped[str] = mapped_column(Unicode(100), nullable=False, default="")
    bao_duong_cuoi: Mapped[date | None] = mapped_column(Date, nullable=True)

    kiem_ke: Mapped[KyKiemKe] = relationship(back_populates="trang_bi")


class VanBan(Base):
    """Sổ văn bản đến/đi, trỏ tới file gốc đã lưu."""

    __tablename__ = "van_ban"

    ma_van_ban: Mapped[str] = mapped_column(Unicode(100), primary_key=True)
    ten_van_ban: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    loai_van_ban: Mapped[str] = mapped_column(Unicode(100), nullable=False, default="")
    noi_gui: Mapped[str] = mapped_column(Unicode(255), nullable=False, default="")
    noi_nhan: Mapped[str] = mapped_column(Unicode(500), nullable=False, default="")
    mo_ta: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    ngay_van_ban: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_path: Mapped[str] = mapped_column(Unicode(1000), nullable=False, default="")
    dang_file: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="")
    # doc_id trong Qdrant - cầu nối sang workflow 1 để tra cứu nội dung.
    doc_id: Mapped[str | None] = mapped_column(Unicode(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
