"""Kết nối SQLite dành riêng cho dữ liệu pháp lý, tách khỏi nghiệp vụ MSSQL."""

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
        if not cfg.database_url.startswith("sqlite+aiosqlite:"):
            raise ValueError("DATABASE_URL chỉ dành cho SQLite pháp lý")
        from pathlib import Path
        from sqlalchemy.engine import make_url
        filename = make_url(cfg.database_url).database
        if filename and filename != ":memory:":
            Path(filename).expanduser().parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            cfg.database_url,
            echo=cfg.database_echo,
            pool_pre_ping=True,
            # SQLite không dùng pool kích thước cố định.
            **({} if cfg.database_url.startswith("sqlite") else {"pool_size": 5, "max_overflow": 10}),
        )
        logger.info("CSDL pháp lý: %s", cfg.database_url.split("://", 1)[0])
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
    """Khởi tạo tám bảng pháp lý SQLite nếu chưa có."""
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_them_cot_con_thieu)


def _them_cot_con_thieu(connection) -> None:
    """Thêm cột mới vào bảng đã tồn tại.

    `create_all` chỉ tạo BẢNG chưa có; bảng cũ thiếu cột thì nó im lặng bỏ qua và
    mọi truy vấn sau đó chết với "no such column" - trên CSDL đang có dữ liệu
    thật, tức là đúng lúc không ai muốn gặp. Dự án chưa dùng migration nên chỗ
    này vá đúng một việc: cột nullable thêm sau, thêm được bằng một câu ALTER.

    Không xoá, không đổi kiểu, không đụng dữ liệu - những việc đó cần migration
    thật chứ không phải một hàm chạy lúc khởi động.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        dang_co = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in dang_co or not column.nullable:
                continue
            kieu = column.type.compile(connection.dialect)
            logger.info("Thêm cột %s.%s (%s)", table.name, column.name, kieu)
            connection.execute(
                text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kieu}"))


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
