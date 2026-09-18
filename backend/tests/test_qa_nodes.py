"""Các node của workflow 1 khi LLM được thay bằng bản giả."""

from __future__ import annotations

import pytest

from app.agents import graph as graph_mod
from app.agents.nodes import qa as qa_mod
from app.agents.prompts import NO_CONTEXT_ANSWER
from app.rag.retrieval import RetrievedChunk
from app.services.cache import CacheService
from app.services.llm import LLMError


def chunk(pid: str, text: str, score: float = 0.9, **payload) -> RetrievedChunk:
    base = {"doc_id": "nd123", "doc_title": "Nghị định 123/2020", "section": "Điều 4",
            "source": "nd123.pdf", "text": text}
    return RetrievedChunk(point_id=pid, text=text, rerank_score=score, rrf_score=0.05,
                          payload={**base, **payload})


@pytest.fixture(autouse=True)
def cache_trong_bo_nho(monkeypatch):
    """Không chạm Redis trong test."""
    cache = CacheService()
    cache._degraded = True
    monkeypatch.setattr(qa_mod, "get_cache", lambda: cache)
    return cache


class FakeLLM:
    def __init__(self, json_response=None, answer="", fail=False):
        self.json_response = json_response
        self.answer = answer
        self.fail = fail
        self.calls: list[list[dict]] = []

    async def chat_json(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        return self.json_response

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        return self.answer


# ------------------------------------------------------------ rewrite ----- #
async def test_rewrite_giai_dai_tu_va_sinh_bien_the(monkeypatch):
    llm = FakeLLM(json_response={
        "standalone_query": "Hoá đơn điện tử có bắt buộc chữ ký số không?",
        "variants": ["quy định chữ ký số hoá đơn điện tử", "hoá đơn điện tử chữ ký số Nghị định 123"],
    })
    monkeypatch.setattr(qa_mod, "get_llm", lambda: llm)

    out = await qa_mod.rewrite_query_node({
        "question": "Thế nó có bắt buộc không?",
        "history": [{"role": "user", "content": "Hoá đơn điện tử là gì?"}],
    })

    assert out["standalone_query"].startswith("Hoá đơn điện tử")
    assert len(out["query_variants"]) == 2


async def test_rewrite_loi_thi_dung_cau_hoi_goc(monkeypatch):
    monkeypatch.setattr(qa_mod, "get_llm", lambda: FakeLLM(fail=True))
    out = await qa_mod.rewrite_query_node({"question": "Mức phạt là bao nhiêu?", "history": []})
    assert out == {"standalone_query": "Mức phạt là bao nhiêu?", "query_variants": []}


async def test_rewrite_dung_lai_ket_qua_da_cache(monkeypatch):
    llm = FakeLLM(json_response={"standalone_query": "câu hỏi chuẩn hoá", "variants": ["a"]})
    monkeypatch.setattr(qa_mod, "get_llm", lambda: llm)
    state = {"question": "câu hỏi", "history": []}

    first = await qa_mod.rewrite_query_node(state)
    second = await qa_mod.rewrite_query_node(state)

    assert first == second
    assert len(llm.calls) == 1  # lần thứ hai lấy từ cache


# ------------------------------------------------------- build context ---- #
def test_context_danh_so_va_tao_trich_dan():
    context, citations = qa_mod.build_context(
        [chunk("p1", "Hoá đơn phải có chữ ký số.", page=3),
         chunk("p2", "Thời hạn nộp thuế là ngày 20.", doc_id="tt80", doc_title="Thông tư 80", section="")],
        max_chars=10_000,
    )
    assert "[1] Nghị định 123/2020 - Điều 4 - tr.3" in context
    assert "[2] Thông tư 80" in context
    assert [c["id"] for c in citations] == [1, 2]
    assert citations[0]["page"] == 3


def test_context_bo_chunk_trung_lap():
    _, citations = qa_mod.build_context(
        [chunk("p1", "Hoá đơn điện tử phải có chữ ký số của người bán."),
         chunk("p2", "chữ ký số của người bán")],  # nằm gọn trong chunk trước
        max_chars=10_000,
    )
    assert len(citations) == 1


def test_context_ton_trong_gioi_han_ky_tu():
    chunks = [chunk(f"p{i}", f"Nội dung điều {i}. " * 30) for i in range(10)]
    context, citations = qa_mod.build_context(chunks, max_chars=1200)
    assert len(context) <= 1400 and 0 < len(citations) < 10


# --------------------------------------------------------- trích dẫn ----- #
def test_chi_giu_trich_dan_duoc_nhac_toi():
    _, citations = qa_mod.build_context([chunk("p1", "A"), chunk("p2", "B"), chunk("p3", "C")], 10_000)
    used = qa_mod.extract_used_citations("Theo [1] và [3] thì đúng.", citations)
    assert [c["id"] for c in used] == [1, 3]


def test_model_quen_danh_so_thi_tra_ve_tat_ca():
    _, citations = qa_mod.build_context([chunk("p1", "A"), chunk("p2", "B")], 10_000)
    assert len(qa_mod.extract_used_citations("Câu trả lời không có số.", citations)) == 2


# ---------------------------------------------------------- generate ----- #
async def test_generate_tra_loi_kem_trich_dan(monkeypatch):
    llm = FakeLLM(answer="Có, hoá đơn điện tử phải có chữ ký số [1].")
    monkeypatch.setattr(qa_mod, "get_llm", lambda: llm)
    context, citations = qa_mod.build_context([chunk("p1", "Hoá đơn phải có chữ ký số.")], 10_000)

    out = await qa_mod.generate_node({"question": "?", "context": context, "citations": citations})

    assert "[1]" in out["answer"]
    assert len(out["used_citations"]) == 1
    assert "generate_ms" in out["trace"]


async def test_khong_co_ngu_canh_thi_khong_goi_llm(monkeypatch):
    llm = FakeLLM(answer="không được phép sinh ra")
    monkeypatch.setattr(qa_mod, "get_llm", lambda: llm)

    out = await qa_mod.generate_node({"question": "giá vàng hôm nay?", "context": "", "citations": []})

    assert out["answer"] == NO_CONTEXT_ANSWER
    assert llm.calls == []          # tuyệt đối không để LLM tự trả lời khi thiếu căn cứ


async def test_llm_hong_thi_bao_loi_co_kiem_soat(monkeypatch):
    monkeypatch.setattr(qa_mod, "get_llm", lambda: FakeLLM(fail=True))
    out = await qa_mod.generate_node({"question": "?", "context": "[1] abc", "citations": []})
    assert "mô hình ngôn ngữ" in out["answer"] and out["error"]


# ------------------------------------------------------------- định tuyến - #
def test_dinh_tuyen_sang_no_context_khi_khong_co_chunk():
    assert graph_mod.route_after_retrieve({"chunks": []}) == "no_context"
    assert graph_mod.route_after_retrieve({"chunks": [chunk("p1", "x")]}) == "build_context"
