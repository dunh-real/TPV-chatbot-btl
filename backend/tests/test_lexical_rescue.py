"""Van cứu từ khoá: chunk khớp nguyên văn thì ngưỡng reranker không được vứt đi.

Reranker chấm CẢ chunk. Một công văn 900 token nói về kiểm kê, dòng cuối ghi
"TỔNG GIÁM ĐỐC Nguyễn Văn Phúc", khi hỏi "Ai là Tổng giám đốc?" chỉ được 0.07 -
dưới ngưỡng 0.1 nên bị loại sạch, và hệ thống trả lời "không có trong tài liệu"
về một thứ có thật trong tài liệu. Van này chặn đúng tình huống đó.

Ranh giới phải giữ: câu hỏi về thứ KHÔNG có trong kho thì vẫn phải trả về rỗng.
"""

from __future__ import annotations

from app.rag.retrieval import FusedHit, HybridRetriever, content_terms


def hit(index: int, text: str, doc_title: str = "Công văn 18/CV-BP") -> FusedHit:
    return FusedHit(
        point_id=f"p{index}",
        rrf_score=0.04 - index * 0.001,
        payload={"text": text, "doc_title": doc_title},
    )


CONG_VAN = (
    "Ban Tổng Giám đốc yêu cầu Trưởng các phòng ban khẩn trương kiểm kê trang "
    "thiết bị và nhân sự, báo cáo trước ngày 25/9/2026. "
    "Nơi nhận: - Như Kính gửi; - Lưu: VT, HC-NS. | TỔNG GIÁM ĐỐC Nguyễn Văn Phúc"
)
BAO_CAO = "Phòng Kỹ thuật hiện có 28 nhân sự, trong đó 24 nhân sự chính thức."


def test_bo_tu_de_hoi_chi_giu_tu_mang_noi_dung():
    assert content_terms("Ai là Tổng giám đốc?") == ["tổng", "giám", "đốc"]
    # Mã hiệu văn bản phải giữ nguyên cả dấu gạch và gạch chéo.
    assert "18/cv-bp" in content_terms("Ai ký công văn 18/CV-BP?")


def test_cuu_chunk_chua_nguyen_van_moi_tu_khoa():
    fused = [hit(0, BAO_CAO, "Báo cáo"), hit(1, CONG_VAN)]
    cuu = HybridRetriever._lexical_rescue("Ai là Tổng giám đốc?", fused, limit=2,
                                          scores={0: 0.002, 1: 0.073})

    assert [c.point_id for c in cuu] == ["p1"]
    assert cuu[0].matched_by == "keyword"


def test_giu_nguyen_diem_that_chu_khong_bao_khong():
    """Hiện 0.0 cho chunk được cứu là nói dối người đọc trace."""
    fused = [hit(0, CONG_VAN)]
    cuu = HybridRetriever._lexical_rescue("Nguyễn Văn Phúc", fused, limit=2, scores={0: 0.0179})

    assert cuu[0].rerank_score == 0.0179


def test_thieu_mot_tu_khoa_thi_khong_cuu():
    """Đòi ĐỦ mọi từ: khớp một phần thì đây chỉ là chunk gần giống, không phải bằng chứng."""
    fused = [hit(0, CONG_VAN)]
    assert HybridRetriever._lexical_rescue("Thuế suất giá trị gia tăng", fused, limit=2) == []


def test_cau_hoi_mot_tu_thi_khong_cuu():
    """Một từ chung mà cũng cứu thì chunk nào cũng lọt, van thành vô dụng."""
    fused = [hit(0, CONG_VAN)]
    assert HybridRetriever._lexical_rescue("kiểm kê", fused, limit=2) != []   # hai từ: được
    assert HybridRetriever._lexical_rescue("kiểm", fused, limit=2) == []      # một từ: không


def test_khong_cuu_qua_han_muc():
    fused = [hit(i, CONG_VAN) for i in range(5)]
    assert len(HybridRetriever._lexical_rescue("Nguyễn Văn Phúc", fused, limit=2)) == 2


def test_xep_theo_diem_reranker_du_deu_duoi_nguong():
    fused = [hit(0, CONG_VAN), hit(1, CONG_VAN)]
    cuu = HybridRetriever._lexical_rescue("Nguyễn Văn Phúc", fused, limit=2,
                                          scores={0: 0.01, 1: 0.06})
    assert [c.point_id for c in cuu] == ["p1", "p0"]
