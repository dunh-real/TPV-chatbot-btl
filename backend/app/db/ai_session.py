"""Kết nối ghi nghiệp vụ AI riêng; ERP vẫn chỉ đọc, không chạy DDL."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.ai_models import reflect

_engine = None
_tables = None
_lock = asyncio.Lock()
erp_task_id = ContextVar("erp_task_id", default=None)
erp_document = ContextVar("erp_document", default=None)
current_task_id = ContextVar("ai_task_id", default=None)
execution_task = ContextVar("queued_execution_task", default=None)
notify_on_finish = ContextVar("notify_on_finish", default=False)


class AINotConfigured(RuntimeError):
    """Chưa cấu hình MSSQL nghiệp vụ; không được quay về SQLite."""


class DocumentNumberConflict(ValueError):
    """Số văn bản đã được sử dụng trong tenant."""


def enabled():
    return bool(get_settings().ai_database_url)


def get_engine():
    global _engine
    url = get_settings().ai_database_url
    if not url.startswith("mssql+aioodbc://"):
        raise AINotConfigured("AI_DATABASE_URL phải là kết nối MSSQL mssql+aioodbc")
    if _engine is None:
        _engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
    return _engine


async def tables():
    global _tables
    if not enabled():
        raise AINotConfigured("Chưa cấu hình AI_DATABASE_URL cho MSSQL nghiệp vụ")
    if _tables is None:
        async with _lock:
            if _tables is None:
                async with get_engine().connect() as conn:
                    _tables = await conn.run_sync(reflect)
    return _tables


@asynccontextmanager
async def repository():
    from app.db.ai_repository import AIRepository
    mapped = await tables()
    async with get_engine().begin() as conn:
        yield AIRepository(conn, mapped)


async def dispose():
    global _engine, _tables
    if _engine is not None:
        await _engine.dispose()
    _engine = _tables = None


async def healthcheck():
    from sqlalchemy import text
    try:
        await tables()
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
