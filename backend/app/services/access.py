"""Tra quyền của một tài khoản, có cache.

VÌ SAO PHẢI CACHE

Mỗi request đi qua middleware, và tra quyền là 3 lượt truy vấn sang SQL Server ở
xa (`dbnews.tpvtech.vn`). Không cache thì mỗi lời gọi chat gánh thêm vài trăm mili
giây chỉ để hỏi lại đúng một câu trả lời không đổi.

TTL ngắn (60 giây) chứ không cache vĩnh viễn: quyền đổi bên ERP thì hệ thống này
phải thấy trong vòng một phút, không bắt ai khởi động lại. Đổi quyền rồi demo
ngay thì chờ một phút, hoặc gọi `invalidate()`.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.db.erp_auth import AccountAccess, ErpAuthRepository
from app.db.erp_session import ErpNotConfigured, erp_session_scope

logger = logging.getLogger(__name__)

TTL_GIAY = 60.0

# khoá -> (thời điểm hết hạn, quyền). `None` là "đã tra và không có tài khoản này",
# cũng được cache: một `X-User-Id` sai sẽ lặp lại ở mọi request của cùng phiên, và
# tra lại ERP từng lần cho một danh tính không tồn tại là phí thuần tuý.
_cache: dict[str, tuple[float, AccountAccess | None]] = {}
_lock = asyncio.Lock()


def invalidate() -> None:
    _cache.clear()


async def resolve_access(identifier: str) -> AccountAccess | None:
    """Quyền của `identifier` (UserId dạng số, username, hoặc email).

    Trả `None` khi: không tra được tài khoản, hoặc ERP chưa cấu hình. Bên gọi
    phân biệt hai trường hợp đó bằng log chứ không bằng giá trị trả về - với tầng
    trên thì cả hai đều là "không có căn cứ để chặn".
    """
    khoa = identifier.strip().lower()
    if not khoa:
        return None

    bay_gio = time.monotonic()
    cached = _cache.get(khoa)
    if cached and cached[0] > bay_gio:
        return cached[1]

    async with _lock:
        # Một request khác có thể vừa nạp xong trong lúc mình chờ khoá.
        cached = _cache.get(khoa)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            async with erp_session_scope() as session:
                repo = ErpAuthRepository(session)
                if khoa.isdigit():
                    access = await repo.find_user(user_id=int(khoa))
                else:
                    access = await repo.find_user(identifier=identifier)
        except ErpNotConfigured:
            logger.debug("Chưa cấu hình ERP nên không tra được quyền")
            return None
        except Exception:  # noqa: BLE001 - ERP hỏng không được làm chết request
            logger.exception("Tra quyền thất bại cho %r", identifier)
            return None

        if access is None:
            logger.info("Không tìm thấy tài khoản %r trong ERP", identifier)
        else:
            logger.debug("Quyền của %s", access.describe())
        _cache[khoa] = (time.monotonic() + TTL_GIAY, access)
        return access


async def list_demo_accounts(tenant_id: int | None) -> list[dict]:
    """Tài khoản để giao diện demo chọn. Hỏng thì trả danh sách rỗng."""
    try:
        async with erp_session_scope() as session:
            return await ErpAuthRepository(session).list_accounts(tenant_id)
    except ErpNotConfigured:
        return []
    except Exception:  # noqa: BLE001
        logger.exception("Không lấy được danh sách tài khoản demo")
        return []
