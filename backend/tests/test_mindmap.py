"""Sơ đồ tư duy: chia mẻ, MAP-REDUCE, vá JSON bị cắt, và hai nhịp của API.

LLM và Qdrant đều được thay bằng bản giả: bài test này soát phần logic của tính
năng - cây có đúng hình dạng không, trần node có được tôn trọng không, nội dung
một mục có được ghi lại để lần sau khỏi sinh lại không - chứ không soát chất
lượng chữ nghĩa mà model viết ra.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.api import mindmap as mindmap_api
from app.documents import mindmap
from app.main import app
from app.services import mindmap_store

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# Đồ giả
# --------------------------------------------------------------------------- #
class LLMGia:
    """Trả lời theo pha, nhận ra pha nào nhờ system prompt."""

    def __init__(self, reduce_raw: str | None = None) -> None:
        self.reduce_raw = reduce_raw
        self.calls: list[str] = []

    async def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        if "tổng hợp cấu trúc" in system:
            self.calls.append("reduce")
            return self.reduce_raw if self.reduce_raw is not None else json.dumps({
                "id": "root",
                "title": "Công văn 105",
                "children": [
                    {"title": "Căn cứ", "children": []},
                    {"title": "Nội dung", "children": [
                        {"title": "Kiểm kê", "children": []},
                        {"title": "Báo cáo", "children": []},
                    ]},
                ],
            }, ensure_ascii=False)
        if "phân tích cấu trúc" in system:
            self.calls.append("map")
            return json.dumps({"nodes": [{"title": "Chủ đề A", "children": []}]},
                              ensure_ascii=False)
        self.calls.append("content")
        return json.dumps({"summary": "Tóm tắt chi tiết của mục.",
                           "key_points": ["Ý 1", "Ý 2"]}, ensure_ascii=False)


class ChunkGia:
    def __init__(self, text: str, label: str) -> None:
        self.text = text
        self._label = label

    def citation_label(self) -> str:
        return self._label


class RetrieverGia:
    def __init__(self) -> None:
        self.filters: list[object] = []

    async def retrieve(self, query, query_filter=None, **kwargs):
        self.filters.append(query_filter)

        class KetQua:
            chunks = [ChunkGia("Nội dung đoạn liên quan.", "Công văn 105 - Mục 1")]

        return KetQua()


class StoreGia:
    """Kho vector giả: một tài liệu, vài chunk."""

    def __init__(self, payloads=None) -> None:
        self.payloads = payloads if payloads is not None else [
            {"text": f"# Mục {i}\nNội dung của mục {i} trong công văn.",
             "doc_title": "Công văn 105", "chunk_index": i}
            for i in range(6)
        ]

    async def scroll_document(self, doc_id, page_size=256):
        return self.payloads

    async def list_documents(self, page_size=512):
        return [{"doc_id": "abc123", "doc_title": "Công văn 105",
                 "doc_type": "cong_van", "chunk_count": len(self.payloads),
                 "ingested_at": 1.0}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def kho(tmp_path, monkeypatch):
    """Mọi cây lưu vào thư mục tạm của test, không đụng data/ của dự án."""
    from app.core.config import get_settings

    cfg = get_settings()
    monkeypatch.setattr(cfg, "mindmap_dir", str(tmp_path / "mindmaps"))
    return tmp_path


@pytest.fixture
def llm(monkeypatch):
    fake = LLMGia()
    monkeypatch.setattr(mindmap, "get_llm", lambda: fake)
    return fake


@pytest.fixture
def he_thong_gia(kho, llm, monkeypatch):
    store = StoreGia()
    retriever = RetrieverGia()
    monkeypatch.setattr(mindmap_api, "get_vector_store", lambda: store)
    monkeypatch.setattr(mindmap_api, "get_retriever", lambda: retriever)
    return {"llm": llm, "store": store, "retriever": retriever}


async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


# --------------------------------------------------------------------------- #
# Chia mẻ
# --------------------------------------------------------------------------- #
def test_chia_me_cat_o_ranh_gioi_muc():
    """Mỗi mẻ bắt đầu bằng một heading - không có mục nào bị chia đôi."""
    texts = [f"# Mục {i}\nnội dung" for i in range(8)]

    batches = mindmap.build_batches(texts)

    assert 1 <= len(batches) <= mindmap.MAP_MAX_BATCHES
    assert all(batch.startswith("# Mục ") for batch in batches)
    # Không mất chữ nào: gộp lại vẫn đủ 8 mục.
    assert sum(batch.count("# Mục ") for batch in batches) == 8


def test_chia_me_khong_vuot_tran():
    batches = mindmap.build_batches([f"# Mục {i}\nnội dung dài" for i in range(200)])

    assert len(batches) <= mindmap.MAP_MAX_BATCHES


def test_chia_me_tai_lieu_rong():
    assert mindmap.build_batches([]) == []


# --------------------------------------------------------------------------- #
# Dựng cây
# --------------------------------------------------------------------------- #
async def test_dung_cay_gan_id_theo_duong_di(llm):
    tree = await mindmap.generate_skeleton(["# A\nnội dung", "# B\nnội dung"], "Công văn 105", 500)

    assert tree["id"] == "root"
    assert [c["id"] for c in tree["children"]] == ["node_1", "node_2"]
    assert [c["id"] for c in tree["children"][1]["children"]] == ["node_2_1", "node_2_2"]
    assert mindmap.count_nodes(tree) == 5


async def test_tran_so_node_cat_theo_bfs(llm, monkeypatch):
    """Vượt trần thì các mục cấp cao được giữ, lá xa gốc bị tỉa."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mindmap_max_nodes", 3)

    tree = await mindmap.generate_skeleton(["# A\nnội dung"], "Công văn 105", 500)

    assert mindmap.count_nodes(tree) == 3
    # Hai mục cấp 2 còn nguyên, mục cấp 3 mới là thứ bị cắt.
    assert [c["title"] for c in tree["children"]] == ["Căn cứ", "Nội dung"]
    assert tree["children"][1]["children"] == []


async def test_reduce_hong_thi_ghep_phang_thay_vi_mat_trang(monkeypatch):
    """Pha MAP đã tốn lượt LLM rồi - REDUCE hỏng không được làm mất kết quả đó."""
    llm = LLMGia(reduce_raw="model nói lảm nhảm, không có JSON nào")
    monkeypatch.setattr(mindmap, "get_llm", lambda: llm)

    tree = await mindmap.generate_skeleton(["# A\nnội dung", "# B\nnội dung"], "Công văn 105", 500)

    assert tree["title"] == "Công văn 105"
    assert [c["title"] for c in tree["children"]] == ["Chủ đề A", "Chủ đề A"]


async def test_json_bi_cat_van_va_lai_duoc(monkeypatch):
    """Cây dài chạm trần max_tokens: đóng nốt ngoặc, giữ phần đã sinh được."""
    day_du = json.dumps({
        "id": "root", "title": "Công văn 105",
        "children": [{"title": "Căn cứ", "children": []},
                     {"title": "Nội dung", "children": []}],
    }, ensure_ascii=False)
    llm = LLMGia(reduce_raw=day_du[: day_du.index('{"title": "Nội dung"')])
    monkeypatch.setattr(mindmap, "get_llm", lambda: llm)

    tree = await mindmap.generate_skeleton(["# A\nnội dung"], "Công văn 105", 500)

    assert [c["title"] for c in tree["children"]] == ["Căn cứ"]


def test_va_json_tra_none_khi_khong_cuu_noi():
    assert mindmap._repair_truncated_json("không phải json") is None


def test_nan_cay_khi_model_tra_ve_chuoi_thay_vi_object():
    tree = mindmap._validate_tree(
        {"root": {"title": "", "children": ["Mục viết tắt", {"title": "Mục đủ"}]}},
        "Công văn 105",
    )

    assert tree["title"] == "Công văn 105"
    assert [c["title"] for c in tree["children"]] == ["Mục viết tắt", "Mục đủ"]


def test_khung_gui_cho_giao_dien_khong_kem_noi_dung():
    """Nội dung mọi mục cộng lại là hàng chục nghìn ký tự - không gửi kèm khung."""
    tree = {"id": "root", "title": "A", "summary": "dài dòng",
            "children": [{"id": "node_1", "title": "B", "children": []}]}

    khung = mindmap.strip_for_api(tree)

    assert "summary" not in khung
    assert khung["has_content"] is True
    assert khung["children"][0]["has_content"] is False


# --------------------------------------------------------------------------- #
# Lưu trữ
# --------------------------------------------------------------------------- #
def test_doc_id_ban_khong_thanh_duong_dan(kho):
    """doc_id đến từ body request - không được để nó trỏ ra ngoài thư mục tenant."""
    with pytest.raises(mindmap_store.MindmapStoreError):
        mindmap_store.save("../../etc/passwd", {"title": "x", "children": []})


def test_luu_va_doc_lai(kho):
    mindmap_store.save("abc123", {"id": "root", "title": "A", "children": []},
                       meta={"doc_title": "Công văn 105"})

    record = mindmap_store.load("abc123")

    assert record["doc_title"] == "Công văn 105"
    assert record["tree"]["title"] == "A"
    assert [item["doc_id"] for item in mindmap_store.list_all()] == ["abc123"]
    assert mindmap_store.delete("abc123") is True
    assert mindmap_store.load("abc123") is None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def test_dung_roi_doc_lai_khong_ton_luot_llm(he_thong_gia):
    res = await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["doc_title"] == "Công văn 105"
    assert body["node_count"] == 5
    assert body["cached"] is False
    assert body["tree"]["children"][0]["title"] == "Căn cứ"
    so_luot = len(he_thong_gia["llm"].calls)

    # Gọi lại: trả bản đã lưu, không thêm lượt LLM nào.
    lai = await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    assert lai.json()["cached"] is True
    assert len(he_thong_gia["llm"].calls) == so_luot


async def test_dung_lai_thi_goi_llm_lai(he_thong_gia):
    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})
    so_luot = len(he_thong_gia["llm"].calls)

    res = await _call("POST", "/api/mindmap/generate",
                      json={"doc_id": "abc123", "regenerate": True})

    assert res.json()["cached"] is False
    assert len(he_thong_gia["llm"].calls) > so_luot


async def test_tai_lieu_khong_co_trong_kho_tra_404(he_thong_gia, monkeypatch):
    monkeypatch.setattr(mindmap_api, "get_vector_store", lambda: StoreGia(payloads=[]))

    res = await _call("POST", "/api/mindmap/generate", json={"doc_id": "khong_co"})

    assert res.status_code == 404


async def test_cay_qua_so_sai_thi_bao_loi_chu_khong_luu(he_thong_gia, monkeypatch):
    """Lưu một cây trống là để người dùng tưởng tài liệu của mình chỉ có ngần ấy."""
    monkeypatch.setattr(mindmap_api, "MIN_NODES", 99)

    res = await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    assert res.status_code == 422
    assert mindmap_store.load("abc123") is None


async def test_noi_dung_muc_sinh_mot_lan_roi_ghi_lai(he_thong_gia):
    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    res = await _call("POST", "/api/mindmap/abc123/section", json={"node_id": "node_2_1"})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "Kiểm kê"
    assert body["summary"] == "Tóm tắt chi tiết của mục."
    assert body["key_points"] == ["Ý 1", "Ý 2"]
    assert body["sources"] == ["Công văn 105 - Mục 1"]
    assert body["cached"] is False
    so_luot = len(he_thong_gia["llm"].calls)

    lai = await _call("POST", "/api/mindmap/abc123/section", json={"node_id": "node_2_1"})

    assert lai.json()["cached"] is True
    assert len(he_thong_gia["llm"].calls) == so_luot
    # Khung cây gửi lần sau đã đánh dấu mục này có nội dung.
    cay = await _call("GET", "/api/mindmap/abc123")
    assert cay.json()["tree"]["children"][1]["children"][0]["has_content"] is True


async def test_truy_hoi_noi_dung_muc_bi_gioi_han_trong_dung_tai_lieu(he_thong_gia):
    """Bỏ lọc doc_id thì mục 'Kết luận' lấy về kết luận của văn bản khác."""
    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    await _call("POST", "/api/mindmap/abc123/section", json={"node_id": "node_1"})

    loc = he_thong_gia["retriever"].filters[-1]
    assert loc is not None
    assert loc.must[0].match.any == ["abc123"]


async def test_muc_khong_co_that_tra_404(he_thong_gia):
    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    res = await _call("POST", "/api/mindmap/abc123/section", json={"node_id": "node_99"})

    assert res.status_code == 404


async def test_danh_sach_nguon_danh_dau_tai_lieu_da_co_so_do(he_thong_gia):
    truoc = await _call("GET", "/api/mindmap/sources")
    assert truoc.json()[0]["has_mindmap"] is False

    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    sau = await _call("GET", "/api/mindmap/sources")
    assert sau.json()[0]["has_mindmap"] is True


async def test_xoa_so_do(he_thong_gia):
    await _call("POST", "/api/mindmap/generate", json={"doc_id": "abc123"})

    res = await _call("DELETE", "/api/mindmap/abc123")

    assert res.status_code == 204
    assert (await _call("GET", "/api/mindmap/abc123")).status_code == 404
    assert (await _call("DELETE", "/api/mindmap/abc123")).status_code == 404
