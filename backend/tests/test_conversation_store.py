"""Collection hội thoại riêng trong Qdrant: bản gốc của lịch sử chat.

Điều phải chứng minh ở đây là điều Redis không làm được: mất cache - restart,
đổi worker, xoá container - mà hội thoại vẫn còn.
"""

from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.rag import embedding as embedding_mod
from app.rag.embedding import HybridEmbedding, SparseEmbedding
from app.services.cache import CacheService
from app.services.conversation import ConversationMemory, ConversationStore

# Vài "chủ đề" cho vector giả: cùng chủ đề thì gần nhau, khác thì vuông góc.
CHU_DE = {"nhân sự": 0, "thiết bị": 1, "nghỉ phép": 2}


def _dense(text: str, size: int) -> list[float]:
    vector = [0.0] * size
    for tu, vi_tri in CHU_DE.items():
        if tu in text.lower():
            vector[vi_tri] = 1.0
    if not any(vector):
        vector[len(CHU_DE)] = 1.0      # không thuộc chủ đề nào, vẫn khác vector 0
    return vector


@pytest.fixture
def env(monkeypatch):
    cfg = Settings()

    def _embed_documents(texts):
        return [HybridEmbedding(dense=_dense(t, cfg.dense_vector_size),
                                lexical=SparseEmbedding(indices=[], values=[]),
                                bm25=SparseEmbedding(indices=[], values=[]))
                for t in texts]

    monkeypatch.setattr(embedding_mod, "embed_documents", _embed_documents)
    monkeypatch.setattr(embedding_mod, "embed_query",
                        lambda text: _embed_documents([text])[0])

    cache = CacheService()
    cache._degraded = True           # không cần Redis thật
    store = ConversationStore(cfg, client=AsyncQdrantClient(":memory:"))
    return ConversationMemory(cache=cache, store=store)


async def test_ghi_roi_doc_lai_dung_thu_tu(env):
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "nhân sự tháng 8?"),
                                    ("assistant", "113 người.")])
    await env.append_turns("ht-1", [("user", "còn thiết bị?"),
                                    ("assistant", "16 đầu thiết bị.")])

    history = await env.store.get_history("ht-1")

    assert [t["role"] for t in history] == ["user", "assistant", "user", "assistant"]
    assert [t["content"] for t in history] == [
        "nhân sự tháng 8?", "113 người.", "còn thiết bị?", "16 đầu thiết bị."]


async def test_mat_cache_van_con_hoi_thoai(env):
    """Đây là lý do tồn tại của collection này."""
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "soạn báo cáo nhân sự DV01"),
                                    ("assistant", "Đã soạn.")])

    # Mô phỏng restart: cache sạch trơn, Qdrant còn nguyên.
    env._cache = CacheService()
    env._cache._degraded = True
    assert await env._cache.get_history("ht-1") == []

    history = await env.get_history("ht-1")

    assert [t["content"] for t in history] == ["soạn báo cáo nhân sự DV01", "Đã soạn."]


async def test_doc_lai_thi_ham_nong_cache(env):
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "hỏi"), ("assistant", "đáp")])
    env._cache = CacheService()
    env._cache._degraded = True

    await env.get_history("ht-1")          # lần này đi xuống Qdrant

    assert len(await env._cache.get_history("ht-1")) == 2


async def test_hai_hoi_thoai_khong_lan_nhau(env):
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "của hội thoại 1")])
    await env.append_turns("ht-2", [("user", "của hội thoại 2")])

    assert [t["content"] for t in await env.store.get_history("ht-1")] == ["của hội thoại 1"]
    assert [t["content"] for t in await env.store.get_history("ht-2")] == ["của hội thoại 2"]


async def test_xoa_thi_xoa_ca_hai_tang(env):
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "hỏi"), ("assistant", "đáp")])

    await env.clear_history("ht-1")

    assert await env.cache.get_history("ht-1") == []
    assert await env.store.get_history("ht-1") == []


async def test_bo_qua_luot_rong_va_vai_la(env):
    await env.store.ensure_collection()
    ghi = await env.store.append_turns("ht-1", [("user", ""), ("system", "x"),
                                                ("user", "câu thật")])

    assert ghi == 1
    assert [t["content"] for t in await env.store.get_history("ht-1")] == ["câu thật"]


async def test_tim_lai_hoi_thoai_cu_theo_ngu_nghia(env):
    """Cái mà một bảng SQL không cho không: tìm theo ý, không theo id."""
    await env.store.ensure_collection()
    await env.append_turns("ht-1", [("user", "báo cáo nhân sự tháng 8")])
    await env.append_turns("ht-2", [("user", "kiểm kê thiết bị quý III")])
    await env.append_turns("ht-3", [("user", "chế độ nghỉ phép")])

    hits = await env.store.search("thiết bị hỏng", limit=1)

    assert hits and hits[0]["content"] == "kiểm kê thiết bị quý III"
    assert hits[0]["conversation_id"] == "ht-2"


async def test_gioi_han_so_luot_tra_ve(env):
    await env.store.ensure_collection()
    for i in range(6):
        await env.append_turns("ht-1", [("user", f"câu {i}")])

    history = await env.store.get_history("ht-1", limit=2)

    assert [t["content"] for t in history] == ["câu 4", "câu 5"]


async def test_qdrant_chet_thi_khong_lam_sap_chat(env, monkeypatch):
    """Đọc lỗi -> trả rỗng và log, không ném ngược lên tầng API."""
    async def _no(*args, **kwargs):
        raise RuntimeError("Qdrant sập")

    monkeypatch.setattr(env.store._client, "scroll", _no)

    assert await env.store.get_history("ht-1") == []


async def test_collection_hoi_thoai_tach_khoi_collection_tai_lieu(env):
    cfg = Settings()

    assert env.store.collection == cfg.qdrant_conversation_collection
    assert env.store.collection != cfg.qdrant_collection
