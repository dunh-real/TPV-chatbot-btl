"""End-to-end workflow 1 trên Qdrant in-memory, model thật, LLM giả.

Kiểm chứng đúng điều mà hybrid hứa hẹn: mỗi nhánh mạnh ở một kiểu truy vấn khác
nhau, RRF gộp lại, reranker đưa đoạn đúng lên đầu.
Chạy: pytest -m slow   (cần GPU + model trong HF cache)
"""

from __future__ import annotations

import pytest
from qdrant_client import AsyncQdrantClient

from app.agents.nodes import qa as qa_mod
from app.core.config import Settings
from app.rag.ingestion import IngestionPipeline
from app.rag.retrieval import HybridRetriever
from app.rag.vectorstore import BM25_VECTOR, DENSE_VECTOR, LEXICAL_VECTOR, QdrantVectorStore
from app.services.cache import CacheService

pytestmark = pytest.mark.slow

CORPUS = {
    "Nghị định 123/2020": """Điều 4. Nguyên tắc lập hoá đơn

Khi bán hàng hoá, cung cấp dịch vụ, người bán phải lập hoá đơn để giao cho người mua.
Hoá đơn điện tử phải có chữ ký số của người bán thì mới có giá trị pháp lý.

Điều 10. Nội dung của hoá đơn

Hoá đơn phải thể hiện tên, địa chỉ, mã số thuế của người bán và người mua.""",

    "Thông tư 80/2021": """Điều 8. Thời hạn nộp hồ sơ khai thuế

Người nộp thuế khai thuế theo tháng phải nộp hồ sơ chậm nhất là ngày 20 của tháng sau.
Trường hợp khai theo quý, thời hạn là ngày cuối cùng của tháng đầu quý sau.""",

    "Quy chế nhân sự TPV": """Chương III. Chế độ nghỉ phép

Nhân viên chính thức được hưởng 12 ngày phép năm, cộng thêm 1 ngày cho mỗi 5 năm làm việc.
Đơn xin nghỉ phép phải gửi trưởng bộ phận trước ít nhất 3 ngày làm việc.

Chương IV. Chế độ công tác phí

Mức khoán công tác phí trong nước là 500.000 đồng một ngày.""",

    "Quy định phụ cấp 2024": """Điều 7. Mức phụ cấp trách nhiệm

Mức phụ cấp trách nhiệm áp dụng từ ngày 01/01/2024 theo bảng dưới đây:

| Chức danh | Mức phụ cấp | Ghi chú |
|---|---|---|
| Giám đốc | 5.000.000 | theo tháng |
| Phó giám đốc | 4.000.000 | theo tháng |
| Trưởng phòng | 3.000.000 | theo tháng |
| Phó phòng | 2.000.000 | theo tháng |
| Nhân viên | 1.000.000 | không áp dụng với thử việc |""",

    "Hướng dẫn phần mềm QLDN": """Mục 2. Đăng nhập hệ thống

Người dùng đăng nhập bằng tài khoản email công ty và mật khẩu do quản trị viên cấp.
Sau 5 lần nhập sai, tài khoản sẽ bị khoá trong 30 phút.""",
}


@pytest.fixture(scope="module")
async def store() -> QdrantVectorStore:
    settings = Settings(qdrant_collection="test_hybrid", rerank_top_n=5,
                        rrf_top_k=20, retrieval_branch_limit=20)
    vector_store = QdrantVectorStore(settings, client=AsyncQdrantClient(":memory:"))
    await vector_store.ensure_collection(recreate=True)

    pipeline = IngestionPipeline(settings, store=vector_store)
    for title, text in CORPUS.items():
        await pipeline.ingest_text(text=text, doc_title=title, source=f"{title}.pdf",
                                   doc_type="quy_dinh")
    return vector_store


@pytest.fixture(scope="module")
def retriever(store) -> HybridRetriever:
    return HybridRetriever(store.settings, store=store)


# ------------------------------------------------------------- ingest ----- #
async def test_moi_chunk_co_du_ba_bieu_dien(store):
    assert await store.count() >= len(CORPUS)

    points, _ = await store.client.scroll("test_hybrid", limit=1, with_vectors=True)
    vectors = points[0].vector
    assert len(vectors[DENSE_VECTOR]) == 1024
    assert len(vectors[LEXICAL_VECTOR].indices) > 0     # sparse học được
    assert len(vectors[BM25_VECTOR].indices) > 0        # sparse thống kê
    assert points[0].payload["section"].startswith(("Điều", "Chương", "Mục"))


async def test_ingest_lai_khong_nhan_ban_du_lieu(store):
    before = await store.count()
    pipeline = IngestionPipeline(store.settings, store=store)
    result = await pipeline.ingest_text(text=CORPUS["Thông tư 80/2021"],
                                        doc_title="Thông tư 80/2021",
                                        source="Thông tư 80/2021.pdf")
    assert await store.count() == before
    assert result.chunk_count > 0


# ------------------------------------------------- từng nhánh tìm kiếm ---- #
async def test_dense_bat_duoc_cau_hoi_dien_dat_khac_han(retriever):
    """Câu hỏi không dùng lại từ nào trong tài liệu ngoài 'phép'."""
    fused, _, _ = await retriever.search(["một năm được bao nhiêu ngày nghỉ?"])
    top = fused[0]
    assert "12 ngày phép" in top.text
    assert DENSE_VECTOR in top.branch_ranks


async def test_bm25_bat_duoc_ma_hieu_van_ban(retriever):
    fused, _, _ = await retriever.search(["Thông tư 80/2021 quy định gì"])
    top = fused[0]
    assert top.payload["doc_title"] == "Thông tư 80/2021"
    assert BM25_VECTOR in top.branch_ranks


async def test_ca_ba_nhanh_deu_tra_ve_ket_qua(retriever):
    _, branch_hits, timings = await retriever.search(["hoá đơn điện tử chữ ký số"])
    assert branch_hits[DENSE_VECTOR] > 0
    assert branch_hits[LEXICAL_VECTOR] > 0
    assert branch_hits[BM25_VECTOR] > 0
    assert {"embed", "search", "rrf"} <= timings.keys()


async def test_rrf_uu_tien_chunk_duoc_nhieu_nhanh_dong_thuan(retriever):
    fused, _, _ = await retriever.search(["thời hạn nộp hồ sơ khai thuế theo tháng"])
    top = fused[0]
    assert "ngày 20" in top.text
    assert len(top.branch_ranks) >= 2      # ít nhất hai nhánh cùng tìm ra


# ------------------------------------------------------------- bảng ------ #
async def test_tra_cuu_duoc_gia_tri_nam_trong_bang(retriever):
    """Giá trị chỉ tồn tại trong một ô của bảng markdown."""
    result = await retriever.retrieve("Phụ cấp trách nhiệm của trưởng phòng là bao nhiêu?")
    assert not result.is_empty
    top = result.chunks[0]
    assert "| Trưởng phòng | 3.000.000 |" in top.text
    # Dòng tiêu đề phải đi kèm, nếu không LLM không biết 3.000.000 là cột nào.
    assert "| Chức danh | Mức phụ cấp | Ghi chú |" in top.text
    assert top.payload["has_table"] is True


async def test_bang_khong_bi_cat_roi_khoi_cau_dan_nhap(retriever):
    result = await retriever.retrieve("mức phụ cấp áp dụng từ ngày nào?")
    top = result.chunks[0]
    assert "01/01/2024" in top.text and "| Giám đốc |" in top.text


# ------------------------------------------------------------ rerank ----- #
async def test_rerank_dua_dung_doan_len_dau(retriever):
    result = await retriever.retrieve("Sau bao nhiêu lần nhập sai mật khẩu thì bị khoá tài khoản?")
    assert "5 lần nhập sai" in result.chunks[0].text
    assert result.chunks[0].rerank_score > 0.5
    assert "rerank" in result.timings_ms


async def test_cau_hoi_ngoai_pham_vi_bi_loai_het(retriever):
    result = await retriever.retrieve("Giá vàng SJC hôm nay bao nhiêu một lượng?")
    assert result.is_empty          # không đoạn nào vượt ngưỡng -> không có gì để bịa


async def test_loc_theo_tai_lieu(retriever, store):
    from app.rag.vectorstore import build_filter

    result = await retriever.retrieve(
        "hoá đơn điện tử",
        query_filter=build_filter(sources=["Quy chế nhân sự TPV.pdf"]),
    )
    for chunk in result.chunks:
        assert chunk.payload["source"] == "Quy chế nhân sự TPV.pdf"


# --------------------------------------------------- toàn bộ workflow ---- #
async def test_workflow_day_du_tra_loi_kem_trich_dan(retriever, monkeypatch):
    class FakeLLM:
        async def chat_json(self, messages, **kwargs):
            return {"standalone_query": "Hoá đơn điện tử có bắt buộc chữ ký số không?",
                    "variants": ["quy định chữ ký số trên hoá đơn điện tử"]}

        async def chat(self, messages, **kwargs):
            assert "NGỮ CẢNH" in messages[-1]["content"]
            return "Có. Hoá đơn điện tử phải có chữ ký số của người bán [1]."

    cache = CacheService()
    cache._degraded = True
    monkeypatch.setattr(qa_mod, "get_llm", FakeLLM)
    monkeypatch.setattr(qa_mod, "get_cache", lambda: cache)
    monkeypatch.setattr(qa_mod, "get_retriever", lambda: retriever)

    from app.agents.graph import build_qa_graph

    result = await build_qa_graph().ainvoke({
        "question": "Hoá đơn điện tử có cần chữ ký số không?",
        "conversation_id": "test",
        "history": [],
        "trace": {},
    })

    assert "chữ ký số" in result["answer"]
    assert result["used_citations"][0]["doc_title"] == "Nghị định 123/2020"
    assert result["trace"]["branch_hits"][DENSE_VECTOR] > 0
    assert len(result["query_variants"]) == 1


async def test_workflow_tra_loi_an_toan_khi_ngoai_pham_vi(retriever, monkeypatch):
    class FakeLLM:
        async def chat_json(self, messages, **kwargs):
            return {"standalone_query": "Giá vàng SJC hôm nay?", "variants": []}

        async def chat(self, messages, **kwargs):
            raise AssertionError("Không được gọi LLM khi không có ngữ cảnh")

    cache = CacheService()
    cache._degraded = True
    monkeypatch.setattr(qa_mod, "get_llm", FakeLLM)
    monkeypatch.setattr(qa_mod, "get_cache", lambda: cache)
    monkeypatch.setattr(qa_mod, "get_retriever", lambda: retriever)

    from app.agents.graph import build_qa_graph

    result = await build_qa_graph().ainvoke({
        "question": "Giá vàng SJC hôm nay bao nhiêu?",
        "conversation_id": "test2", "history": [], "trace": {},
    })

    assert "không tìm thấy thông tin" in result["answer"].lower()
    assert result["used_citations"] == []
    assert result["trace"]["no_context"] is True
