"""Kết nối CSDL nghiệp vụ - dùng chung cho SQL Server và SQLite.

`DATABASE_URL` quyết định đích:
    mssql+pyodbc://user:pass@host:1433/tpv?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    sqlite+aiosqlite:///./data/demo.db        (dev/demo, không cần server)

Toàn bộ truy vấn viết bằng SQLAlchemy nên đổi đích chỉ là đổi chuỗi kết nối.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        _engine = create_async_engine(
            cfg.database_url,
            echo=cfg.database_echo,
            pool_pre_ping=True,
            # SQLite không dùng pool kích thước cố định.
            **({} if cfg.database_url.startswith("sqlite") else {"pool_size": 5, "max_overflow": 10}),
        )
        logger.info("CSDL nghiệp vụ: %s", cfg.database_url.split("://", 1)[0])
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(settings), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session tự commit khi thoát êm, rollback khi có lỗi."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency cho FastAPI."""
    async with session_scope() as session:
        yield session


async def create_all() -> None:
    """Tạo bảng nếu chưa có - dùng cho demo/test, không thay cho migration."""
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def healthcheck() -> bool:
    from sqlalchemy import text

    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("CSDL không phản hồi: %s", exc)
        return False
