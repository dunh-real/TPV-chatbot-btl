"""Workflow 3, nhánh nguồn là MỘT TÀI LIỆU TẢI LÊN.

Nhánh này đổi bản chất của bảo đảm về con số, nên các ca ở đây chốt đúng chỗ đó:

    nhánh CSDL : số phải truy được về một TRƯỜNG DỮ LIỆU của ERP
    nhánh này  : số phải xuất hiện NGUYÊN VĂN trong tài liệu nguồn

Yếu hơn, và cố ý yếu hơn - nhưng phải yếu đúng mức đã tuyên bố, không yếu hơn
nữa. Cụ thể: model vẫn không được bịa ra số mới, và mọi phép cộng/trừ/tính tỷ lệ
đều tạo ra số không có trong tài liệu nên phải bị chặn.

Bất biến quan trọng nhất, dùng chung với nhánh CSDL: thứ gửi cho model và tập số
hợp lệ của van chắn PHẢI bằng nhau. Ở nhánh này cả hai đều là nguyên văn tài
liệu; có test giữ để ai đó tối ưu prompt (cắt bớt phần gửi model) thì đỏ ngay.
"""

from __future__ import annotations

import pytest

from app.agents.nodes import drafting_doc as dd
from app.documents.verify import check_numbers, collect_known_numbers

TAI_LIEU = """BÁO CÁO KẾT QUẢ KIỂM KÊ

Tổng số nhân sự: 28 người, trong đó 24 chính thức.
Tổng số trang thiết bị: 79, trong đó 72 hoạt động tốt, 07 cần bảo dưỡng.
Tỷ lệ thiết bị tốt đạt 91,1%.
"""


# --------------------------------------------- bảo đảm về con số --------- #
def test_so_co_trong_tai_lieu_thi_qua():
    known = collect_known_numbers({"noi_dung": TAI_LIEU})
    assert check_numbers("Tổng số trang thiết bị là 79, trong đó 72 tốt.", known).ok
    # Tỷ lệ CHÉP LẠI từ tài liệu thì hợp lệ - nó có nguyên văn trong nguồn.
    assert check_numbers("Tỷ lệ thiết bị tốt đạt 91,1%.", known).ok


def test_so_model_tu_tinh_bi_bat():
    """Mọi phép tính đều đẻ ra số không có trong tài liệu - đó là cái van bắt."""
    known = collect_known_numbers({"noi_dung": TAI_LIEU})
    # 79 - 72 = 7 thì "7" có trong tài liệu ("07" chuẩn hoá về "7"), nên KHÔNG
    # bắt được. Ghi lại giới hạn này thay vì giả vờ là không có.
    assert check_numbers("Còn 7 thiết bị chưa đạt.", known).ok
    # Nhưng số thật sự mới thì bị chặn.
    assert check_numbers("Tổng chi phí 15.000.000 đồng.", known).unverified == ["15.000.000"]
    assert check_numbers("Có 133 thiết bị.", known).unverified == ["133"]


def test_so_bia_hoan_toan_bi_bat():
    known = collect_known_numbers({"noi_dung": TAI_LIEU})
    assert not check_numbers("Dự kiến năm 2031 cần bổ sung 456 thiết bị.", known).ok


# --------------------------------------------- đọc tài liệu -------------- #
async def test_khong_co_file_thi_hoi_lai():
    ket_qua = await dd.read_document_node({"file_id": ""})

    assert ket_qua["missing_input"] == ["file_id"]
    assert ket_qua["error"]


async def test_file_khong_ton_tai_thi_bao_loi():
    ket_qua = await dd.read_document_node({"file_id": "upload:khong-co-that.docx"})

    assert ket_qua["error"]
    assert "data" not in ket_qua


async def test_doc_duoc_thi_giu_nguyen_van(tmp_path, monkeypatch):
    ket_qua = await _doc_thu(tmp_path, monkeypatch, TAI_LIEU)

    assert ket_qua["data"]["nguon_so_lieu"] == "tai_lieu"
    # Nguyên văn, không tóm tắt: tóm tắt là chèn một lượt LLM vào giữa nguồn và
    # van chắn, và từ đó không con số nào còn truy về được.
    assert ket_qua["data"]["noi_dung"] == TAI_LIEU
    assert "không đối chiếu với CSDL" in " ".join(ket_qua["data_notes"])


async def test_tai_lieu_qua_dai_thi_cat_va_noi_ro(tmp_path, monkeypatch):
    dai = "x" * (dd.MAX_DOC_CHARS + 500)
    ket_qua = await _doc_thu(tmp_path, monkeypatch, dai)

    assert len(ket_qua["data"]["noi_dung"]) == dd.MAX_DOC_CHARS
    # Cắt âm thầm thì người đọc tưởng báo cáo đã bao trùm cả tài liệu.
    assert any("ký tự đầu" in note for note in ket_qua["data_notes"])


# --------------------------------------------- dàn ý --------------------- #
async def test_dan_y_hong_van_ra_bao_cao(monkeypatch):
    """LLM chết ở bước dàn ý thì vẫn phải ra văn bản, không trả lỗi trắng."""
    class LLMHong:
        async def chat_json(self, *a, **k):
            raise RuntimeError("LLM chết")

    monkeypatch.setattr(dd, "get_llm", lambda: LLMHong())
    ket_qua = await dd.outline_node(
        {"data": {"ten_tai_lieu": "a.docx", "noi_dung": TAI_LIEU}})

    muc = ket_qua["template"]["fields"]["sections"]
    assert len(muc) >= 2
    assert all(m["type"] == "llm" for m in muc)


async def test_dan_y_bam_tai_lieu(monkeypatch):
    class LLMGia:
        async def chat_json(self, *a, **k):
            return {"tieu_de": "Báo cáo kiểm kê",
                    "muc": [{"id": "m1", "tieu_de": "I. NHÂN SỰ", "huong_dan": "Nêu nhân sự"},
                            {"id": "m2", "tieu_de": "II. THIẾT BỊ", "huong_dan": "Nêu số lượng"}]}

    monkeypatch.setattr(dd, "get_llm", lambda: LLMGia())
    ket_qua = await dd.outline_node(
        {"data": {"ten_tai_lieu": "a.docx", "noi_dung": TAI_LIEU}})

    assert ket_qua["template"]["ten_bao_cao"] == "Báo cáo kiểm kê"
    assert [m["title"] for m in ket_qua["template"]["fields"]["sections"]] == [
        "I. NHÂN SỰ", "II. THIẾT BỊ"]
    # Không có file mẫu -> `build_docx` phải tự dựng bằng code.
    assert ket_qua["template"]["file_path"] == ""


# --------------------------------------------- render ------------------- #
async def test_tap_so_hop_le_bang_dung_thu_gui_cho_model(monkeypatch):
    """Bất biến: `source_data` của mỗi mục PHẢI bằng thứ model vừa đọc.

    Rộng hơn thì van chắn cho qua những số model không hề nhìn thấy; hẹp hơn thì
    nó chặn nhầm câu đúng. Bài test giữ đúng dấu bằng đó.
    """
    da_gui: list[str] = []

    class LLMGia:
        async def chat_json(self, messages, **k):
            da_gui.append(messages[-1]["content"])
            return {"paragraphs": ["Tổng số trang thiết bị là 79."]}

    monkeypatch.setattr(dd, "get_llm", lambda: LLMGia())
    state = {
        "data": {"noi_dung": TAI_LIEU, "ten_tai_lieu": "a.docx"},
        "template": {"ten_bao_cao": "BC", "fields": {"sections": [
            {"id": "m1", "title": "I. A", "type": "llm", "narrative": "x"}]}},
    }
    ket_qua = await dd.render_node(state)

    muc = ket_qua["sections"][0]
    assert muc["source_data"] == {"noi_dung": TAI_LIEU}
    assert TAI_LIEU in da_gui[0]
    # Và tập số suy ra từ `source_data` phải công nhận câu model vừa viết.
    known = collect_known_numbers(muc["source_data"])
    assert check_numbers(muc["paragraphs"][0], known).ok


async def test_mot_muc_hong_khong_keo_do_ca_bao_cao(monkeypatch):
    class LLMDoiLuc:
        def __init__(self):
            self.n = 0

        async def chat_json(self, *a, **k):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("hỏng mục đầu")
            return {"paragraphs": ["Nội dung mục hai."]}

    monkeypatch.setattr(dd, "get_llm", lambda: LLMDoiLuc())
    state = {
        "data": {"noi_dung": TAI_LIEU, "ten_tai_lieu": "a.docx"},
        "template": {"ten_bao_cao": "BC", "fields": {"sections": [
            {"id": "m1", "title": "I. A", "type": "llm", "narrative": "x"},
            {"id": "m2", "title": "II. B", "type": "llm", "narrative": "y"}]}},
    }
    ket_qua = await dd.render_node(state)

    assert len(ket_qua["sections"]) == 2
    assert ket_qua["sections"][0]["paragraphs"] == []
    assert ket_qua["sections"][1]["paragraphs"] == ["Nội dung mục hai."]


async def test_co_loi_thi_khong_chay_tiep(monkeypatch):
    assert await dd.outline_node({"error": "hỏng"}) == {}
    assert await dd.render_node({"error": "hỏng"}) == {}
    assert await dd.render_node({"missing_input": ["file_id"]}) == {}


# --------------------------------------------- tiện ích ------------------ #
async def _doc_thu(tmp_path, monkeypatch, noi_dung: str) -> dict:
    """Dựng một file thật rồi cho `read_document_node` đọc, không cần Qdrant."""
    from app.services import storage

    duong_dan = tmp_path / "tai_lieu_thu.md"
    duong_dan.write_text(noi_dung, encoding="utf-8")

    class RefGia:
        file_id = "upload:tai_lieu_thu.md"
        name = "tai_lieu_thu.md"
        path = duong_dan

    monkeypatch.setattr(storage, "resolve", lambda *a, **k: RefGia())

    class KetQua:
        text = noi_dung

    class ConverterGia:
        def convert(self, *a, **k):
            return KetQua()

    import app.rag.converter as conv

    monkeypatch.setattr(conv, "get_converter", lambda: ConverterGia())
    return await dd.read_document_node({"file_id": "upload:tai_lieu_thu.md"})
