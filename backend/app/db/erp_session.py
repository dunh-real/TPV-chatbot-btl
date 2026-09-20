"""Kết nối tới CSDL ERP - CHỈ ĐỌC.

Quyền được cấp cho hệ thống này là chỉ đọc trên một danh sách bảng cố định. Chốt
chặn dưới đây không thừa: quyền trên máy chủ có thể bị cấp rộng ra lúc nào không
biết, còn `ALLOWED_TABLES` và `_reject_writes` thì nằm ngay trong mã nguồn và đi
theo mọi lần triển khai.

Hai lớp chặn:
  1. `_reject_writes` - mọi câu lệnh không phải đọc đều bị chặn trước khi gửi đi.
  2. `erp_session_scope` - phiên không autoflush, không commit, rollback khi đóng.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ErpNotConfigured(RuntimeError):
    """ERP_DATABASE_URL để trống - không có nguồn số liệu để đọc."""


class ReadOnlyViolation(RuntimeError):
    """Có chỗ nào đó định ghi vào ERP. Đây là lỗi lập trình, không phải lỗi dữ liệu."""


# Danh sách bảng được phép đọc. Ngoài danh sách này là ngoài phạm vi cho phép,
# kể cả khi login có quyền đọc cả CSDL.
ALLOWED_TABLES = frozenset({
    "AbpRoles", "AbpUserRoles",
    "AI_ChatConversations", "AI_ChatMessages",
    "Asm_AssetAssignment", "Asm_AssetAssignmentDetail",
    "Asm_AssetCategories", "Asm_Assets",
    "Dms_Employee",  # mở thêm: Hrm_EmployeeProfile.EmployeeId bắt buộc trỏ tới đây
    "Dms_WorkDepartment", "Dms_WorkPosition",
    "Hrm_EmployeeProfile",
})

# Từ khoá mở đầu một câu lệnh ghi/đổi cấu trúc. Chặn theo từ khoá mở đầu thay vì
# tìm chuỗi bất kỳ trong câu: chữ "update" nằm trong tên cột `LastModificationTime`
# hay trong một tham số chuỗi đều không phải là lệnh ghi.
_WRITE_STATEMENT = re.compile(
    r"^\s*(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|CREATE|ALTER|RENAME"
    r"|GRANT|REVOKE|DENY|BACKUP|RESTORE|EXEC|EXECUTE|SP_[A-Z_]*)\b",
    re.IGNORECASE,
)
# Chú thích đầu câu lệnh không được dùng để che một lệnh ghi phía sau.
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def _reject_writes(conn, cursor, statement: str, parameters, context, executemany) -> None:
    if _WRITE_STATEMENT.match(_COMMENTS.sub(" ", statement)):
        raise ReadOnlyViolation(
            f"CSDL ERP chỉ được đọc, câu lệnh bị chặn: {statement.strip()[:120]}"
        )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_erp_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        if not cfg.erp_database_url:
            raise ErpNotConfigured(
                "Chưa đặt ERP_DATABASE_URL nên không đọc được số liệu phòng ban / "
                "thiết bị / nhân sự."
            )
        _engine = create_async_engine(
            cfg.erp_database_url,
            echo=cfg.erp_echo,
            pool_pre_ping=True,
            **({} if cfg.erp_database_url.startswith("sqlite")
               else {"pool_size": 5, "max_overflow": 10}),
        )
        event.listen(_engine.sync_engine, "before_cursor_execute", _reject_writes)
        logger.info("CSDL ERP (chỉ đọc): %s", cfg.erp_database_url.split("://", 1)[0])
    return _engine


def get_erp_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_erp_engine(settings),
            expire_on_commit=False,
            class_=AsyncSession,
            # Không có gì để flush: phiên này không bao giờ mang đối tượng bẩn.
            autoflush=False,
        )
    return _session_factory


def erp_available(settings: Settings | None = None) -> bool:
    return bool((settings or get_settings()).erp_database_url)


@asynccontextmanager
async def erp_session_scope() -> AsyncIterator[AsyncSession]:
    """Phiên đọc ERP. Luôn rollback khi đóng - không có gì để commit."""
    async with get_erp_session_factory()() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def dispose_erp_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _session_factory = None, None
