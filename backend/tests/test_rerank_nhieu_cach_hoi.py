"""Reranker chấm theo MỌI cách diễn đạt câu hỏi, lấy điểm cao nhất.

Vì sao cần: `AITeamVN/Vietnamese_Reranker` gần như so khớp từ vựng chứ không
hiểu diễn đạt khác. Đo ngày 20/09/2026 trên cùng một chunk của `CV-105-BGD`:

    "hạn nộp báo cáo là khi nào"        -> 0,0073   (dưới ngưỡng, bị vứt)
    "báo cáo gửi về trước ngày nào"     -> 0,9399

Hai câu hỏi CÙNG một điều. Văn bản viết "Báo cáo gửi về ... trước ngày
20/9/2026", nên ai tình cờ dùng đúng chữ của văn bản thì được trả lời, ai dùng
chữ khác thì nhận "không tìm thấy" về một thứ có thật trong tài liệu.

Đã thử và loại (đừng thử lại, số đo ở `CAN-LAM-TIEP.md §6b`):
  - gắn số hiệu làm tiền tố chunk: 0,0001 -> 0,0345, vẫn dưới ngưỡng
  - mở rộng câu hỏi bằng từ vựng lấy từ chính chunk (PRF): chữa được ca hỏng
    nhưng thổi câu NGOÀI KHO từ 0,0000 lên 0,9082 - mất hẳn khả năng nói
    "không có trong tài liệu"

Ranh giới bài test này giữ: lấy max qua các biến thể KHÔNG được biến van thành
cửa mở toang. Biến thể là cách hỏi khác của cùng một câu, không phải cái cớ để
mọi chunk đều lọt.
"""

from __future__ import annotations

import pytest

from app.rag.reranker import CrossEncoderReranker


class RerankerGia(CrossEncoderReranker):
    """Thay phần chạy model bằng bảng điểm khai sẵn, để test không cần GPU."""

    def __init__(self, bang_diem: dict[tuple[str, str], float], settings=None) -> None:
        super().__init__(settings=settings)
        self.bang_diem = bang_diem
        self.da_cham: list[str] = []

    def score(self, query: str, documents: list[str]) -> list[float]:
        self.da_cham.append(query)
        return [self.bang_diem.get((query, d), 0.0) for d in documents]


CHUNK_DUNG = "Báo cáo gửi về Phòng Hành chính nhân sự trước ngày 20/9/2026"
CHUNK_LAC = "Quy định về chế độ nghỉ phép hằng năm"

HOI_GOC = "hạn nộp báo cáo là khi nào"
HOI_HANH_CHINH = "báo cáo gửi về trước ngày nào"

# Số đo thật ngày 20/09/2026.
BANG = {
    (HOI_GOC, CHUNK_DUNG): 0.0073,
    (HOI_HANH_CHINH, CHUNK_DUNG): 0.9399,
    (HOI_GOC, CHUNK_LAC): 0.0001,
    (HOI_HANH_CHINH, CHUNK_LAC): 0.0002,
}


def test_mot_cach_hoi_van_nhu_cu():
    """Truyền một chuỗi thì hành vi không đổi - đường lùi phải còn nguyên."""
    rr = RerankerGia(BANG)
    ket_qua = rr.rerank(HOI_GOC, [CHUNK_DUNG], score_threshold=0.1)

    assert ket_qua == []
    assert rr.da_cham == [HOI_GOC]


def test_cach_hoi_hanh_chinh_cuu_duoc_chunk_dung():
    """Chunk trả lời được MỘT cách hỏi là đủ để qua ngưỡng."""
    rr = RerankerGia(BANG)
    ket_qua = rr.rerank([HOI_GOC, HOI_HANH_CHINH], [CHUNK_DUNG], score_threshold=0.1)

    assert len(ket_qua) == 1
    assert ket_qua[0].score == pytest.approx(0.9399)


def test_chunk_lac_de_van_bi_loai():
    """Ranh giới: thêm biến thể KHÔNG được kéo chunk lạc đề qua ngưỡng."""
    rr = RerankerGia(BANG)
    ket_qua = rr.rerank([HOI_GOC, HOI_HANH_CHINH], [CHUNK_LAC], score_threshold=0.1)

    assert ket_qua == []


def test_lay_max_chu_khong_phai_trung_binh():
    """Trung bình (0,47) cũng qua ngưỡng, nên phải chốt đúng là MAX.

    Khác biệt lộ ra khi có biến thể lạc đề: trung bình thì một biến thể tồi kéo
    tụt chunk đúng, mà biến thể là thứ máy tự sinh.
    """
    rr = RerankerGia(BANG)
    ket_qua = rr.rerank([HOI_GOC, HOI_HANH_CHINH], [CHUNK_DUNG], score_threshold=0.1)

    assert ket_qua[0].score == pytest.approx(0.9399)


def test_bien_the_trung_lap_chi_cham_mot_lan():
    """Mỗi cách hỏi là một lượt forward trên toàn bộ ứng viên - đừng chấm thừa."""
    rr = RerankerGia(BANG)
    rr.rerank([HOI_GOC, HOI_GOC, HOI_HANH_CHINH, ""], [CHUNK_DUNG], score_threshold=0.0)

    assert rr.da_cham == [HOI_GOC, HOI_HANH_CHINH]


def test_tran_so_cach_hoi(monkeypatch):
    """`rerank_max_queries` là trần chi phí, phải thật sự chặn."""
    rr = RerankerGia(BANG)
    monkeypatch.setattr(rr.settings, "rerank_max_queries", 1)
    rr.rerank([HOI_GOC, HOI_HANH_CHINH], [CHUNK_DUNG], score_threshold=0.0)

    assert rr.da_cham == [HOI_GOC]


def test_khong_co_cach_hoi_nao_hop_le():
    rr = RerankerGia(BANG)
    assert rr.score_best(["", "   "], [CHUNK_DUNG]) == [0.0]
    assert rr.score_best([HOI_GOC], []) == []
