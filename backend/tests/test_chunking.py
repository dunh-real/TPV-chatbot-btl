"""Chunker: cấu trúc văn bản pháp quy, bảng biểu, và các ngưỡng token."""

from __future__ import annotations

from app.core.config import Settings
from app.rag.chunking import Chunker, approximate_token_counter, isolate_tables, table_header

VAN_BAN = """Chương I. Quy định chung

Điều 1. Phạm vi điều chỉnh

Nghị định này quy định việc quản lý, sử dụng hoá đơn khi bán hàng hoá.

Điều 4. Nguyên tắc lập hoá đơn

Khi bán hàng hoá, người bán phải lập hoá đơn giao cho người mua.
Hoá đơn điện tử phải có chữ ký số của người bán.

Chương II. Xử phạt vi phạm

Điều 20. Mức phạt

Phạt tiền từ 2.000.000 đồng đến 4.000.000 đồng với hành vi lập sai hoá đơn.
"""

BANG = """Điều 7. Mức phụ cấp

Mức phụ cấp áp dụng từ 01/01/2024 như sau:

| Chức danh | Mức phụ cấp | Ghi chú |
|---|---|---|
""" + "\n".join(f"| Nhân viên {i} | {i}.000.000 | theo tháng |" for i in range(1, 30))


def chunker(**overrides) -> Chunker:
    return Chunker(Settings(**overrides), token_counter=approximate_token_counter)


# ------------------------------------------------------------- cấu trúc --- #
def test_tach_theo_dieu_va_giu_tieu_de_muc():
    chunks = chunker().split(VAN_BAN, {"doc_id": "nd123"})
    assert any("Điều 4." in c.text for c in chunks)
    # Tiêu đề mục được nhắc lại trong nội dung chunk để LLM định vị được.
    assert any(c.text.startswith("Điều") or c.text.startswith("Chương") for c in chunks)
    assert all(c.metadata["doc_id"] == "nd123" for c in chunks)


def test_khong_gop_xuyen_chuong():
    chunks = chunker().split(VAN_BAN)
    chapters = {c.chapter for c in chunks}
    assert "Chương I. Quy định chung" in chapters
    assert "Chương II. Xử phạt vi phạm" in chapters
    # Không chunk nào chứa nội dung của cả hai chương.
    for chunk in chunks:
        assert not ("Phạm vi điều chỉnh" in chunk.text and "Mức phạt" in chunk.text)


def test_gop_manh_vun_nhung_van_nho_moi_muc_da_gop():
    chunks = chunker().split(VAN_BAN)
    gop = next(c for c in chunks if len(c.sections) > 1)
    assert gop.section_label.startswith("Điều 1.")
    assert gop.section_label.endswith("Điều 4. Nguyên tắc lập hoá đơn")


def test_chi_so_chunk_lien_tuc():
    chunks = chunker().split(VAN_BAN)
    assert [c.index for c in chunks] == list(range(len(chunks)))


# ---------------------------------------------------------------- bảng ---- #
def test_khoi_bang_duoc_co_lap_thanh_don_vi_rieng():
    text = "Đoạn văn trước.\n| A | B |\n|---|---|\n| 1 | 2 |\nĐoạn văn sau."
    isolated = isolate_tables(text)
    assert "\n\n| A | B |" in isolated
    assert "| 1 | 2 |\n\n" in isolated


def test_bang_dai_bi_cat_nhung_moi_phan_giu_dong_tieu_de():
    cfg = dict(chunk_min_tokens=20, chunk_ideal_tokens=80, chunk_max_tokens=120, chunk_hard_cap=150)
    chunks = chunker(**cfg).split(BANG)
    phan_bang = [c for c in chunks if c.has_table]

    assert len(phan_bang) > 1                       # bảng thực sự bị cắt
    for chunk in phan_bang:
        assert "| Chức danh | Mức phụ cấp | Ghi chú |" in chunk.text
    # Không mất dòng dữ liệu nào.
    assert sum(c.text.count("| Nhân viên ") for c in chunks) == 29


def test_bang_ngan_khong_bi_cat_doi():
    text = "Điều 7. Phụ cấp\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    chunks = chunker().split(text)
    assert len(chunks) == 1 and chunks[0].has_table


def test_bang_khong_bi_gop_khoang_trang_can_cot():
    text = "| Chức danh   | Mức   |\n|---|---|\n| Giám đốc    | 5.000 |"
    chunks = chunker().split(text)
    assert "| Chức danh   | Mức   |" in chunks[0].text


def test_dong_tieu_de_bang_nhan_dien_ca_khi_thieu_dong_phan_cach():
    assert table_header(["| A | B |", "|---|---|", "| 1 | 2 |"]) == ["| A | B |", "|---|---|"]
    assert table_header(["| A | B |", "| 1 | 2 |"]) == ["| A | B |"]


# --------------------------------------------------------- ngưỡng token --- #
def test_ton_trong_tran_tren_va_co_chong_lan():
    body = "\n\n".join(
        f"Đoạn số {i} nói về nghĩa vụ kê khai thuế của doanh nghiệp trong kỳ tính thuế." * 3
        for i in range(60)
    )
    cfg = dict(chunk_min_tokens=50, chunk_ideal_tokens=200, chunk_max_tokens=300,
               chunk_hard_cap=400, chunk_overlap=40)
    chunks = chunker(**cfg).split(body)

    assert len(chunks) > 1
    assert all(c.token_count <= 400 for c in chunks)      # không vượt hard cap sau merge
    duoi = chunks[0].text.split()[-6:]
    assert " ".join(duoi) in chunks[1].text               # có chồng lấn


def test_van_ban_khong_co_heading_van_chunk_duoc():
    chunks = chunker().split("Một đoạn văn bản thường, không có tiêu đề mục nào cả.")
    assert len(chunks) == 1 and chunks[0].section == ""


def test_van_ban_rong():
    assert chunker().split("   \n\n  ") == []
