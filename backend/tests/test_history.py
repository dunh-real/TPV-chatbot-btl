"""Khối lịch sử hội thoại nhét vào prompt: cắt đúng số lượt, đổi đúng nhãn."""

from __future__ import annotations

from app.agents.history import DEFAULT_TURNS, EMPTY, format_history


def test_khong_co_lich_su_thi_bao_chua_co():
    assert format_history(None) == EMPTY
    assert format_history([]) == EMPTY


def test_doi_nhan_vai_sang_tieng_viet():
    text = format_history([{"role": "user", "content": "soạn báo cáo DV01"},
                           {"role": "assistant", "content": "Đã soạn."}])
    assert text == "Người dùng: soạn báo cáo DV01\nTrợ lý: Đã soạn."


def test_chi_lay_nhung_luot_gan_nhat():
    history = [{"role": "user", "content": f"câu {i}"} for i in range(10)]
    text = format_history(history, turns=3)

    assert text.count("\n") == 2
    assert "câu 9" in text and "câu 6" not in text


def test_turns_bang_khong_thi_khong_lay_gi():
    assert format_history([{"role": "user", "content": "x"}], turns=0) == EMPTY


def test_mac_dinh_bon_luot_du_cho_hai_vong_hoi_dap():
    assert DEFAULT_TURNS == 4
