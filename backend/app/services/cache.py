"""Redis: bộ nhớ hội thoại + cache tạm cho pipeline RAG.

Redis hỏng không được làm sập chat, nên mọi thao tác đều "best effort": lỗi kết
nối sẽ tự chuyển sang bộ nhớ tiến trình (dict) và chỉ ghi log cảnh báo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

HISTORY_PREFIX = "chat:history:"
CACHE_PREFIX = "rag:cache:"


def cache_key(namespace: str, *parts: str) -> str:
    digest = hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_PREFIX}{namespace}:{digest}"


class CacheService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._degraded = not self.settings.cache_enabled
        # Dự phòng khi Redis không dùng được: (giá trị, thời điểm hết hạn)
        self._fallback: dict[str, tuple[Any, float]] = {}

    async def _get_client(self):
        if self._degraded:
            return None
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    self.settings.redis_url, encoding="utf-8", decode_responses=True
                )
                await self._client.ping()
            except Exception as exc:
                logger.warning("Redis không khả dụng (%s) - dùng cache trong tiến trình", exc)
                self._client = None
                self._degraded = True
                return None
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --------------------------------------------------------- cache K/V -- #
    async def get_json(self, key: str) -> Any | None:
        client = await self._get_client()
        if client is None:
            value, expires_at = self._fallback.get(key, (None, 0.0))
            return value if expires_at > time.time() else None
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.debug("Đọc cache lỗi: %s", exc)
            return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl or self.settings.cache_ttl_seconds
        client = await self._get_client()
        if client is None:
            self._fallback[key] = (value, time.time() + ttl)
            return
        try:
            await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as exc:
            logger.debug("Ghi cache lỗi: %s", exc)

    # ------------------------------------------------------ hội thoại ----- #
    async def get_history(self, conversation_id: str, limit: int | None = None) -> list[dict[str, str]]:
        """Lấy các lượt gần nhất theo thứ tự thời gian tăng dần."""
        limit = limit or self.settings.history_max_turns
        key = f"{HISTORY_PREFIX}{conversation_id}"
        client = await self._get_client()
        if client is None:
            return list(self._fallback.get(key, ([], 0.0))[0])[-limit:]
        try:
            raw_items = await client.lrange(key, -limit, -1)
            return [json.loads(item) for item in raw_items]
        except Exception as exc:
            logger.debug("Đọc lịch sử lỗi: %s", exc)
            return []

    async def append_turn(self, conversation_id: str, role: str, content: str) -> None:
        key = f"{HISTORY_PREFIX}{conversation_id}"
        entry = {"role": role, "content": content, "ts": time.time()}
        client = await self._get_client()
        if client is None:
            history = list(self._fallback.get(key, ([], 0.0))[0])
            history.append(entry)
            self._fallback[key] = (
                history[-self.settings.history_max_turns * 2 :],
                time.time() + self.settings.cache_ttl_seconds,
            )
            return
        try:
            await client.rpush(key, json.dumps(entry, ensure_ascii=False))
            await client.ltrim(key, -self.settings.history_max_turns * 2, -1)
            await client.expire(key, self.settings.cache_ttl_seconds * 24)
        except Exception as exc:
            logger.debug("Ghi lịch sử lỗi: %s", exc)

    async def clear_history(self, conversation_id: str) -> None:
        key = f"{HISTORY_PREFIX}{conversation_id}"
        client = await self._get_client()
        if client is None:
            self._fallback.pop(key, None)
            return
        try:
            await client.delete(key)
        except Exception as exc:
            logger.debug("Xoá lịch sử lỗi: %s", exc)


_cache: CacheService | None = None


def get_cache() -> CacheService:
    global _cache
    if _cache is None:
        _cache = CacheService()
    return _cache
