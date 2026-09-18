"""Reciprocal Rank Fusion gộp 3 nhánh tìm kiếm."""

from __future__ import annotations

from qdrant_client import models

from app.rag.retrieval import reciprocal_rank_fusion


def point(pid: str, score: float) -> models.ScoredPoint:
    return models.ScoredPoint(id=pid, version=0, score=score, payload={"text": f"noi dung {pid}"})


def test_chunk_xuat_hien_o_nhieu_nhanh_duoc_uu_tien():
    branches = [
        ("dense", 1.0, [point("a", 0.9), point("b", 0.8), point("c", 0.7)]),
        ("lexical", 1.0, [point("c", 5.0), point("a", 3.0)]),
        ("bm25", 1.0, [point("d", 9.0), point("c", 4.0)]),
    ]
    fused = reciprocal_rank_fusion(branches, k=60)
    # c đứng hạng 3/1/2 ở ba nhánh -> tổng điểm cao hơn a (hạng 1 ở một nhánh).
    assert fused[0].point_id == "c"
    assert fused[0].branch_ranks == {"dense": 3, "lexical": 1, "bm25": 2}


def test_trong_so_tung_nhanh_co_tac_dung():
    branches_equal = [
        ("dense", 1.0, [point("a", 0.9)]),
        ("bm25", 1.0, [point("b", 9.0)]),
    ]
    assert reciprocal_rank_fusion(branches_equal)[0].point_id in {"a", "b"}

    branches_weighted = [
        ("dense", 2.0, [point("a", 0.9)]),
        ("bm25", 0.5, [point("b", 9.0)]),
    ]
    assert reciprocal_rank_fusion(branches_weighted)[0].point_id == "a"


def test_giu_hang_tot_nhat_khi_mot_nhanh_lap_lai():
    # Cùng một nhánh chạy cho nhiều biến thể truy vấn.
    branches = [
        ("dense", 1.0, [point("x", 0.5), point("a", 0.4)]),
        ("dense", 1.0, [point("a", 0.9)]),
    ]
    fused = {h.point_id: h for h in reciprocal_rank_fusion(branches)}
    assert fused["a"].branch_ranks["dense"] == 1     # hạng tốt nhất được giữ
    assert fused["a"].rrf_score > fused["x"].rrf_score  # nhưng vẫn cộng dồn cả hai lần


def test_diem_giam_dan_theo_hang_va_gioi_han_limit():
    points = [point(str(i), 1.0 - i / 100) for i in range(10)]
    fused = reciprocal_rank_fusion([("dense", 1.0, points)], k=60, limit=3)
    assert len(fused) == 3
    assert [h.point_id for h in fused] == ["0", "1", "2"]
    assert fused[0].rrf_score > fused[1].rrf_score > fused[2].rrf_score


def test_khong_co_ket_qua_thi_tra_ve_rong():
    assert reciprocal_rank_fusion([("dense", 1.0, []), ("bm25", 1.0, [])]) == []
