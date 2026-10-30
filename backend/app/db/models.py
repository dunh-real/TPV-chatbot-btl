"""Các bảng pháp lý và bằng chứng câu trả lời; chỉ lưu trong SQLite."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, Unicode, UnicodeText, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Văn bản quy phạm theo phiên bản (app.legal)
#
# Tách điều khoản TRỪU TƯỢNG (legal_component: "khoản 3 Điều 17 NĐ 379") khỏi các
# PHIÊN BẢN theo thời gian của nó (legal_version, có valid_from/valid_to), và ghi
# mỗi lệnh sửa đổi thành một bản ghi (legal_action) - mô hình Work/Expression của
# FRBR/LRMoo mà Akoma Ntoso, ELI và SAT-Graph RAG dùng, cài bằng bảng quan hệ.
# Ngày NULL nghĩa là CHƯA BIẾT, không phải "luôn luôn" - không đoán ngày hiệu lực.
# --------------------------------------------------------------------------- #
class LegalDocument(Base):
    __tablename__ = "legal_document"

    # Định danh pháp lý ổn định: "vn:nd:379-2026-nd-cp". Khác doc_id (băm file):
    # cùng một văn bản tải lên hai bản file khác nhau vẫn là một văn bản.
    legal_id: Mapped[str] = mapped_column(Unicode(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(Unicode(64), nullable=False, index=True)
    so_hieu: Mapped[str] = mapped_column(Unicode(80), nullable=False, default="", index=True)
    loai: Mapped[str] = mapped_column(Unicode(60), nullable=False, default="")
    ten: Mapped[str] = mapped_column(Unicode(1000), nullable=False, default="")
    source: Mapped[str] = mapped_column(Unicode(500), nullable=False, default="")
    ngay_ban_hanh: Mapped[date | None] = mapped_column(Date, nullable=True)
    ngay_hieu_luc: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Văn bản này sửa đổi những văn bản nào (tên như trong tiêu đề Điều), JSON list.
    sua_doi: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                 onupdate=func.now())


class LegalComponent(Base):
    __tablename__ = "legal_component"

    # "vn:nd:379-2026-nd-cp:dieu-17:khoan-3:diem-a"
    component_id: Mapped[str] = mapped_column(Unicode(240), primary_key=True)
    legal_id: Mapped[str] = mapped_column(Unicode(160), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(Unicode(240), nullable=True, index=True)
    unit: Mapped[str] = mapped_column(Unicode(20), nullable=False)        # Phần|Chương|Mục|Điều|Khoản|Điểm
    so: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="")
    # Thứ tự giữa anh em; Điều chèn "vào sau Điều 31" nhận 31.5 nên không phải đánh số lại.
    thu_tu: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    path: Mapped[str] = mapped_column(Unicode(500), nullable=False, default="")


class LegalVersion(Base):
    __tablename__ = "legal_version"

    version_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    component_id: Mapped[str] = mapped_column(Unicode(240), nullable=False, index=True)
    # Chữ CỦA RIÊNG đơn vị (dòng tiêu đề + thân), không gồm đơn vị con.
    text: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    sha256: Mapped[str] = mapped_column(Unicode(64), nullable=False, default="")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    created_by_action: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class LegalAction(Base):
    __tablename__ = "legal_action"

    action_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    # Văn bản sửa đổi và đơn vị chứa lệnh ("khoản 5 Điều 1 Luật 23/2026").
    source_legal_id: Mapped[str] = mapped_column(Unicode(160), nullable=False, index=True)
    source_component_id: Mapped[str] = mapped_column(Unicode(240), nullable=False, default="")
    instruction: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    op: Mapped[str] = mapped_column(Unicode(20), nullable=False)          # sua_doi|bo_sung|bai_bo|thay_cum_tu
    target_law: Mapped[str] = mapped_column(Unicode(500), nullable=False, default="")
    target_address: Mapped[str] = mapped_column(Unicode(200), nullable=False, default="")
    target_legal_id: Mapped[str | None] = mapped_column(Unicode(160), nullable=True, index=True)
    target_component_id: Mapped[str | None] = mapped_column(Unicode(240), nullable=True)
    new_text: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # applied | pending_target (chưa có văn bản đích trong kho) | quarantined | rejected
    status: Mapped[str] = mapped_column(Unicode(20), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LegalCitation(Base):
    __tablename__ = "legal_citation"

    citation_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    from_component_id: Mapped[str] = mapped_column(Unicode(240), nullable=False, index=True)
    # Địa chỉ như viết trong văn bản ("khoản 3 Điều 13 Luật Quản lý, sử dụng tài sản công").
    reference: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    to_component_id: Mapped[str | None] = mapped_column(Unicode(240), nullable=True, index=True)


class AnswerLog(Base):
    __tablename__ = "answer_log"

    answer_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Unicode(64), nullable=False, default="", index=True)
    question: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    answer: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    model: Mapped[str] = mapped_column(Unicode(200), nullable=False, default="")
    # fresh | stale (nguồn đã bị sửa đổi) | rechecked
    status: Mapped[str] = mapped_column(Unicode(20), nullable=False, default="fresh", index=True)
    stale_reason: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="")
    # Ngày bản sửa đổi có hiệu lực - câu trả lời đúng tới trước ngày này.
    stale_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AnswerEvidence(Base):
    __tablename__ = "answer_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    answer_id: Mapped[str] = mapped_column(Unicode(64), nullable=False, index=True)
    component_id: Mapped[str] = mapped_column(Unicode(240), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(Unicode(64), nullable=False, default="", index=True)
    sha256: Mapped[str] = mapped_column(Unicode(64), nullable=False, default="")


class LegalChangeEvent(Base):
    __tablename__ = "legal_change_event"

    event_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(Unicode(64), nullable=False, index=True)
    target_component_id: Mapped[str] = mapped_column(Unicode(240), nullable=False, default="")
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    affected_answers: Mapped[str] = mapped_column(UnicodeText, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
