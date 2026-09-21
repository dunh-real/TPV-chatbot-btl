"""Workflow 2: parse có định dạng, dàn ý, rule engine cấu trúc, và ba nhánh LLM.

Rule engine ở workflow này soát MỌI loại tài liệu nên không có tiêu chí nào kiểu
"phải là Times New Roman" hay "phải có quốc hiệu". Nó chỉ trả lời hai câu đo được
từ chính tệp: tài liệu được tổ chức thế nào, và cách trình bày có nhất quán không.
"""

from __future__ import annotations

import pytest

from app.agents.nodes import document as doc_nodes
from app.documents.chinh_ta import load_chinh_ta, soat_chinh_ta
from app.documents.outline import build_outline, parse_marker
from app.documents.parser import Block, DocumentStructure, PageGeometry, parse_document
from app.documents.rules import RuleEngine, load_rules

CONG_VAN = """CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc

CÔNG TY TPV
BAN GIÁM ĐỐC

Số: 105/CV-BGĐ

Hà Nội, ngày 05 tháng 9 năm 2026

CÔNG VĂN
V/v tổng hợp, báo cáo tình trạng trang thiết bị

Kính gửi: Các phòng ban, đơn vị trực thuộc

Ban Giám đốc yêu cầu các đơn vị rà soát nhân sự tính đến ngày 30/9/2026.

Báo cáo gửi về Phòng Hành chính quản trị trước ngày 20/9/2026.

Nơi nhận:
- Như trên;
- Lưu: VT.

GIÁM ĐỐC
"""

HUONG_DAN = """# Hướng dẫn vận hành hệ thống

## 1. Mục đích

Tài liệu mô tả quy trình vận hành hằng ngày của trung tâm dữ liệu.

## 2. Phạm vi

Áp dụng cho toàn bộ máy chủ đang khai thác.

#### 2.1. Máy chủ ứng dụng

Kiểm tra nhật ký mỗi sáng.

## 4. Quy trình

a) Kiểm tra nhật ký

b) Sao lưu dữ liệu

d) Báo cáo kết quả

## 2. Phạm vi
"""


@pytest.fixture
def cong_van(tmp_path):
    path = tmp_path / "CV-105.md"
    path.write_text(CONG_VAN, encoding="utf-8")
    return path


@pytest.fixture
def huong_dan(tmp_path):
    path = tmp_path / "huong_dan.md"
    path.write_text(HUONG_DAN, encoding="utf-8")
    return path


def _docx_noi_dung(tmp_path, name="tai_lieu.docx", *, lech_phong=False, lech_co=False,
                   so_doan_lech=1, them_doan=0, align=None, line=1.15, after=6):
    """DOCX tối thiểu: một tiêu đề, ba mục đánh số, vài đoạn nội dung.

    Đủ dài để phép đo "nhất quán" có cơ sở - dưới `min_blocks` đoạn thì rule engine
    báo chưa đủ dữ liệu chứ không phán.
    """
    docx = pytest.importorskip("docx")
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm, Pt

    document = docx.Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(20), Mm(20)
    section.left_margin, section.right_margin = Mm(30), Mm(20)

    def dat(text, *, font="Times New Roman", size=13, bold=False, canh=None):
        paragraph = document.add_paragraph(text)
        run = paragraph.runs[0]
        run.font.name, run.font.size, run.font.bold = font, Pt(size), bold
        pf = paragraph.paragraph_format
        pf.line_spacing, pf.space_before, pf.space_after = line, Pt(3), Pt(after)
        paragraph.alignment = canh if canh is not None else WD_ALIGN_PARAGRAPH.JUSTIFY
        return paragraph

    dat("QUY CHẾ QUẢN LÝ THIẾT BỊ", bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)
    dat("I. QUY ĐỊNH CHUNG", bold=True, canh=WD_ALIGN_PARAGRAPH.LEFT)
    dat("Quy chế này quy định việc quản lý, sử dụng thiết bị của đơn vị kể từ ngày ban hành.")
    dat("Mọi cá nhân được giao thiết bị phải chịu trách nhiệm bảo quản trong thời gian sử dụng.")
    dat("II. TRÁCH NHIỆM", bold=True, canh=WD_ALIGN_PARAGRAPH.LEFT)
    for thu_tu in range(so_doan_lech):
        dat(f"Phòng Kỹ thuật kiểm kê định kỳ mỗi quý một lần và lập biên bản số {thu_tu + 1}.",
            font="Arial" if lech_phong else "Times New Roman",
            size=11 if lech_co else 13,
            canh=align)
    dat("Phòng Hành chính tổng hợp kết quả kiểm kê để báo cáo Ban Giám đốc trước ngày 20 hằng tháng.")
    for thu_tu in range(them_doan):
        dat(f"Đoạn nội dung bình thường thứ {thu_tu + 1}, viết đúng phông và cỡ chữ của tài liệu.")
    dat("III. HIỆU LỰC", bold=True, canh=WD_ALIGN_PARAGRAPH.LEFT)
    dat("Quy chế có hiệu lực kể từ ngày ký và thay thế các quy định trước đây của đơn vị.")

    path = tmp_path / name
    document.save(path)
    return path


def _check(path):
    structure = parse_document(path)
    return structure, RuleEngine().check(structure)


def _rules(result) -> set[str]:
    return {f.rule for f in result.findings}


# ---------------------------------------------------------------- parser --- #
def test_parse_docx_lay_duoc_phong_co_chu_va_le(tmp_path):
    structure = parse_document(_docx_noi_dung(tmp_path, lech_phong=True, lech_co=True))

    assert structure.source_format == "docx"
    assert structure.has_format_info is True
    lech = next(b for b in structure.blocks if b.font == "Arial")
    assert lech.size_pt == 11.0
    assert structure.geometry is not None and structure.geometry.measured is False


def test_bang_giu_dung_vi_tri_trong_tai_lieu(tmp_path):
    """Bảng nằm ngay dưới tiêu đề mục phải ở ngay dưới tiêu đề đó.

    python-docx trả `document.paragraphs` rồi `document.tables` thành hai danh sách
    rời; đọc theo đó thì mọi bảng bị dồn xuống cuối, mục nào có nội dung là một
    bảng hoá ra "mục rỗng", còn marker trích dẫn [n] thì trỏ sang chỗ khác.
    """
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_heading("Mục có bảng", level=1)
    bang = document.add_table(rows=1, cols=2)
    bang.cell(0, 0).text, bang.cell(0, 1).text = "Chỉ tiêu", "Số lượng"
    document.add_heading("Mục có chữ", level=1)
    document.add_paragraph("Nội dung của mục thứ hai.")

    path = tmp_path / "co_bang.docx"
    document.save(path)
    structure = parse_document(path)
    _, result = _check(path)

    assert [b.kind for b in structure.blocks] == ["heading", "table", "heading", "paragraph"]
    assert "structure.empty_section" in result.passed


def test_parse_markdown_doc_duoc_cap_tieu_de(huong_dan):
    structure = parse_document(huong_dan)

    assert structure.source_format == "text"
    assert structure.has_format_info is False
    assert structure.blocks[0].kind == "heading" and structure.blocks[0].style == "Heading 1"
    # "## Mục" và đoạn ngay dưới nó là hai khối khác nhau dù cách nhau một dòng.
    assert structure.blocks[1].text == "1. Mục đích"
    assert structure.blocks[2].kind == "paragraph"


def test_parse_txt_khong_doc_dau_thang_thanh_tieu_de(tmp_path):
    path = tmp_path / "ghi_chu.txt"
    path.write_text("# đây chỉ là ký tự thăng trong ghi chú\n\nĐoạn hai.", encoding="utf-8")
    structure = parse_document(path)

    assert all(b.kind == "paragraph" for b in structure.blocks)


def test_dinh_dang_khong_ho_tro(tmp_path):
    path = tmp_path / "a.pptx"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="chỉ xử lý"):
        parse_document(path)


# ----------------------------------------------------------------- dàn ý -- #
@pytest.mark.parametrize("text,kind,ordinal", [
    ("1. Tình hình chung", "digit", 1),
    ("1.2. Chi tiết", "digit", 2),
    ("II. TRÁCH NHIỆM", "roman", 2),
    ("b) Sao lưu dữ liệu", "letter", 2),
    ("Chương III - Điều khoản", "word", 3),
])
def test_doc_duoc_phan_danh_so(text, kind, ordinal):
    marker = parse_marker(text)
    assert marker is not None and marker.kind == kind and marker.ordinal == ordinal


@pytest.mark.parametrize("text", [
    "2026 là năm bản lề của kế hoạch",      # năm, không phải số mục
    "1.5 triệu đồng đã được chi trong quý",  # số liệu
    "Đoạn văn bình thường không đánh số",
])
def test_khong_bat_nham_so_lieu_thanh_so_muc(text):
    assert parse_marker(text) is None


def test_cap_muc_theo_thu_tu_xuat_hien_chu_khong_theo_kieu_danh_so(tmp_path):
    """"I." nông hơn "1." trong tài liệu này, nhưng tài liệu khác có thể ngược lại."""
    path = tmp_path / "vb.md"
    path.write_text("I. PHẦN MỘT\n\n1. Mục nhỏ\n\nNội dung của mục nhỏ.\n\n"
                    "II. PHẦN HAI\n\nNội dung phần hai.", encoding="utf-8")
    outline = build_outline(parse_document(path))

    assert outline.explicit is False
    assert [(h.text, h.level) for h in outline.headings] == [
        ("I. PHẦN MỘT", 1), ("1. Mục nhỏ", 2), ("II. PHẦN HAI", 1)]


def test_doan_dai_co_danh_so_khong_phai_tieu_de(tmp_path):
    path = tmp_path / "vb.md"
    path.write_text("TIÊU ĐỀ\n\n1. " + "Trong tháng 8 năm 2026 đơn vị đã hoàn thành. " * 4,
                    encoding="utf-8")
    outline = build_outline(parse_document(path))

    assert outline.headings == []
    assert outline.markers          # vẫn là một dòng đánh số, vẫn kiểm liên tục


def test_tieu_de_bo_qua_bang_bo_cuc_o_dau_tai_lieu():
    structure = DocumentStructure(
        blocks=[Block(id="T00", text="CÔNG TY TPV | CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
                      kind="table"),
                Block(id="P00", text="BÁO CÁO KIỂM KÊ QUÝ III", bold=True)],
        source_format="docx",
    )
    outline = build_outline(structure)

    assert outline.title is not None and outline.title.block_id == "P00"


# ----------------------------------------------------------- rule engine -- #
def test_tai_lieu_khong_co_tieu_de_bi_bao(tmp_path):
    path = tmp_path / "ghi_chu.txt"
    path.write_text("Đoạn mở đầu chạy thẳng vào nội dung mà không có tiêu đề nào cả.",
                    encoding="utf-8")
    _, result = _check(path)

    assert "structure.title" in _rules(result)


def test_nhay_cap_tieu_de_bi_bao(huong_dan):
    _, result = _check(huong_dan)
    nhay = [f for f in result.findings if f.rule == "structure.heading_levels"]

    assert nhay and nhay[0].actual == "cấp 4" and nhay[0].expected == "cấp 3"


# --------------------------------------------------------- đánh số: ĐÃ TẮT -- #
# Bộ tiêu chí mặc định không còn kiểm tính liên tục của đánh số: nó phải đoán kiểu
# đánh số trước khi so được, và đoán sai thì báo oan đúng vào văn bản soạn chuẩn.
# Lý do đầy đủ nằm trong `config/rules/chung.yaml`.
#
# Code `_check_numbering` vẫn còn và vẫn được kiểm ở đây qua một bộ tiêu chí bật
# tay - để bật lại được mà không phải viết lại từ đầu.

NUMBERING_ON = {"structure": {"numbering": {"severity": "warning",
                                            "message": "Đánh số mục không liên tục"}}}


def _check_numbering(path):
    structure = parse_document(path)
    return RuleEngine(rules=NUMBERING_ON, name="danh_so").check(structure)


def test_bo_tieu_chi_mac_dinh_khong_con_kiem_danh_so(huong_dan):
    """Văn bản có đánh số đứt quãng thật cũng không bị bộ mặc định kêu nữa."""
    _, result = _check(huong_dan)

    assert not [f for f in result.findings if f.rule == "structure.numbering"]
    assert "structure.numbering" not in result.passed
    # Cũng không kể nó vào "chưa kiểm được": bộ này vốn không có tiêu chí đó.
    assert not [s for s in result.skipped if s.startswith("structure.numbering")]


def test_danh_so_dut_quang_bi_bao_khi_bat_tay(huong_dan):
    result = _check_numbering(huong_dan)
    so = [f for f in result.findings if f.rule == "structure.numbering"]

    assert {f.actual for f in so} >= {"4.", "d)"}


def test_moi_kieu_danh_so_la_mot_day_rieng(tmp_path):
    """"a)" không nối tiếp "1." - trộn hai dãy vào nhau thì tài liệu nào cũng sai."""
    path = tmp_path / "vb.md"
    path.write_text("TÀI LIỆU\n\n1. Mục một\n\na) Ý nhỏ\n\nb) Ý nhỏ nữa\n\n2. Mục hai",
                    encoding="utf-8")

    assert "structure.numbering" in _check_numbering(path).passed


def test_danh_sach_bo_qua_chu_d_khong_bi_bao(tmp_path):
    """a, b, c, d, e: bảng chữ cái tiếng Việt có "đ" nhưng rất ít danh sách dùng nó."""
    path = tmp_path / "vb.md"
    path.write_text("TÀI LIỆU\n\n" + "\n\n".join(f"{c}) Ý thứ {c}" for c in "abcde"),
                    encoding="utf-8")

    assert "structure.numbering" in _check_numbering(path).passed


def test_bang_chu_cai_khong_co_f(tmp_path):
    """"... d) đ) e) g) h)" là dãy ĐÚNG chuẩn - tiếng Việt không có chữ "f"."""
    path = tmp_path / "vb.md"
    path.write_text("TÀI LIỆU\n\n" + "\n\n".join(
        f"{c}) Ý thứ {c}" for c in ["a", "b", "c", "d", "đ", "e", "g", "h"]), encoding="utf-8")

    assert "structure.numbering" in _check_numbering(path).passed


def test_chu_in_hoa_trung_so_la_ma_van_doc_duoc(tmp_path):
    """"C." và "D." khớp pattern La Mã trước; bỏ chúng đi thì dãy A-E trông như đứt."""
    for chu in "CDM":
        marker = parse_marker(f"{chu}. Nội dung mục")
        assert marker is not None and marker.kind == "letter"

    path = tmp_path / "vb.md"
    path.write_text("TÀI LIỆU\n\n" + "\n\n".join(
        f"{c}. Phần {c}" for c in ["A", "B", "C", "D", "Đ", "E"]), encoding="utf-8")

    outline = build_outline(parse_document(path))
    assert [h.marker for h in outline.headings] == ["A.", "B.", "C.", "D.", "Đ.", "E."]
    assert "structure.numbering" in _check_numbering(path).passed


def test_muc_rong_va_trung_ten_bi_bao(huong_dan):
    _, result = _check(huong_dan)

    assert "structure.empty_section" in _rules(result)      # "2. Phạm vi" cuối file
    assert "structure.duplicate_heading" in _rules(result)


def test_muc_cha_dung_ngay_tren_muc_con_khong_phai_muc_rong(tmp_path):
    path = tmp_path / "vb.md"
    path.write_text("# Tài liệu\n\n## Chương một\n\n### Mục nhỏ\n\nNội dung của mục nhỏ.",
                    encoding="utf-8")
    _, result = _check(path)

    assert "structure.empty_section" in result.passed


def test_doan_qua_dai_chi_ghi_nhan(tmp_path):
    path = tmp_path / "vb.md"
    path.write_text("# Tài liệu\n\n" + "câu văn dài. " * 200, encoding="utf-8")
    _, result = _check(path)
    dai = [f for f in result.findings if f.rule == "structure.paragraph_length"]

    assert dai and dai[0].severity == "info"       # không phải lỗi, chỉ là ghi nhận


def test_tai_lieu_trinh_bay_dong_deu_thi_dat(tmp_path):
    _, result = _check(_docx_noi_dung(tmp_path))

    assert "consistency.font" in result.passed
    assert "consistency.size_pt" in result.passed
    assert "consistency.alignment" in result.passed
    assert "page.size_mm" in result.passed and "page.margin_mm" in result.passed
    assert not [f for f in result.findings if f.rule.startswith("consistency.")]


def test_mot_doan_lac_phong_va_co_chu_bi_chi_dung_cho(tmp_path):
    _, result = _check(_docx_noi_dung(tmp_path, lech_phong=True, lech_co=True))
    phong = [f for f in result.findings if f.rule == "consistency.font"]
    co = [f for f in result.findings if f.rule == "consistency.size_pt"]

    assert phong and phong[0].actual == "Arial" and phong[0].expected == "Times New Roman"
    assert co and co[0].actual == "11pt" and co[0].expected == "13pt"
    assert phong[0].block_id and phong[0].quote      # chỉ ra đúng khối nào lệch


def test_lech_qua_nhieu_cho_thi_gop_mot_dong(tmp_path):
    """Đổ ra hai chục dòng giống nhau thì người đọc bỏ qua cả phần này."""
    _, result = _check(_docx_noi_dung(tmp_path, lech_phong=True, so_doan_lech=6, them_doan=8))
    phong = [f for f in result.findings if f.rule == "consistency.font"]

    assert len(phong) == 1 and "6 đoạn" in phong[0].message
    assert "Arial" in phong[0].actual and "Times New Roman" in phong[0].actual


def test_dong_ngan_khong_bi_do_canh_le_nhung_van_bi_do_phong(tmp_path):
    """"Số: 42/BC-DV02" canh trái giữa một tài liệu canh đều là cố ý, không phải lỗi.

    Nhưng một dòng ngắn lạc phông thì vẫn là lạc phông - hai chiều này khác nhau.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    docx = pytest.importorskip("docx")
    from docx.shared import Pt

    document = docx.Document()

    def dat(text, *, font="Times New Roman", canh=WD_ALIGN_PARAGRAPH.JUSTIFY):
        paragraph = document.add_paragraph(text)
        paragraph.alignment = canh
        run = paragraph.runs[0]
        run.font.name, run.font.size = font, Pt(13)
        paragraph.paragraph_format.line_spacing = 1.15

    dat("QUY CHẾ THỬ NGHIỆM", canh=WD_ALIGN_PARAGRAPH.CENTER)
    for i in range(5):
        dat(f"Đoạn văn xuôi thứ {i + 1} đủ dài để được coi là phần nội dung của tài liệu này.")
    dat("Số: 42/BC-DV02", canh=WD_ALIGN_PARAGRAPH.LEFT)       # dòng ngắn, canh khác
    dat("Hà Nội, ngày 30 tháng 9", font="Arial", canh=WD_ALIGN_PARAGRAPH.LEFT)

    path = tmp_path / "ngan.docx"
    document.save(path)
    _, result = _check(path)

    assert "consistency.alignment" not in _rules(result)
    assert "consistency.font" in _rules(result)


def test_khong_nhom_nao_du_dong_thi_bao_chung(tmp_path):
    """Nửa này một kiểu, nửa kia một kiểu: không có chuẩn để chỉ ra "chỗ lệch"."""
    _, result = _check(_docx_noi_dung(tmp_path, lech_phong=True, so_doan_lech=4))
    phong = [f for f in result.findings if f.rule == "consistency.font"]

    assert len(phong) == 1 and "kiểu khác nhau" in phong[0].message
    assert phong[0].expected == "thống nhất một kiểu"


def test_it_doan_qua_thi_khong_phan_ve_nhat_quan(tmp_path):
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="TIÊU ĐỀ", bold=True),
                Block(id="P01", text="Một đoạn nội dung duy nhất trong cả tài liệu.",
                      font="Arial", size_pt=11)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=20, bottom_mm=20,
                              left_mm=30, right_mm=20),
        source_format="docx",
    )
    result = RuleEngine().check(structure)

    assert not [f for f in result.findings if f.rule.startswith("consistency.")]
    assert any("consistency.font" in s for s in result.skipped)


def test_kho_giay_la_bi_ghi_nhan():
    def check(width, height):
        return RuleEngine().check(DocumentStructure(
            blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
            geometry=PageGeometry(width_mm=width, height_mm=height, top_mm=20,
                                  bottom_mm=20, left_mm=30, right_mm=20),
            source_format="docx",
        ))

    assert "page.size_mm" in check(210, 297).passed
    assert "page.size_mm" in check(297, 210).passed        # A4 xoay ngang vẫn là A4
    assert "page.size_mm" in {f.rule for f in check(180, 240).findings}


def test_le_lech_vai_phan_nghin_mm_khong_bi_bao():
    """Word lưu lề theo inch: đặt đúng 1cm thì đọc về là 9.9975mm.

    Không có dung sai thì văn bản đặt lề sát mức tối thiểu đều bị báo sai - đúng
    kiểu cảnh báo mà người dùng học cách phớt lờ, rồi phớt lờ luôn cảnh báo thật.
    """
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=9.9975,
                              bottom_mm=9.9975, left_mm=10.0025, right_mm=9.9975),
        source_format="docx",
    )
    result = RuleEngine().check(structure)

    assert not [f for f in result.findings if f.rule == "page.margin_mm"]
    assert "page.margin_mm" in result.passed


def test_le_sai_that_van_bi_bao():
    """Dung sai không được nuốt lỗi thật: chữ sát mép giấy vẫn phải kêu."""
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=20, bottom_mm=20,
                              left_mm=30, right_mm=3.0),
        source_format="docx",
    )
    result = RuleEngine().check(structure)
    sai = [f for f in result.findings if f.rule == "page.margin_mm"]

    assert sai and sai[0].actual == "3.0mm" and "phải" in sai[0].message


def test_le_pdf_chi_kiem_tra_phia_dang_tin():
    """Lề PDF suy từ vùng chữ: lề dưới/phải của trang ngắn là vô nghĩa."""
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=22, bottom_mm=200,
                              left_mm=32, right_mm=90, measured=True),
        source_format="pdf_digital",
    )
    result = RuleEngine().check(structure)

    assert not [f for f in result.findings if f.rule == "page.margin_mm"]
    assert any("margin_mm.bottom" in s for s in result.skipped)
    assert any("margin_mm.right" in s for s in result.skipped)


def test_van_ban_scan_thi_bo_qua_trinh_bay_chu_khong_bao_dat():
    scan = DocumentStructure(
        blocks=[Block(id="P00", text="BÁO CÁO KIỂM KÊ", bold=True),
                Block(id="P01", text="Nội dung sau khi OCR.")],
        source_format="pdf_scan",
    )
    result = RuleEngine().check(scan)

    assert result.status == "partial"            # KHÔNG phải "done"
    assert "consistency.font" in result.skipped
    assert "structure.title" in result.passed    # phần tổ chức vẫn kiểm được
    assert "scan" in result.reason.lower()


def test_tep_rong_thi_noi_thang_la_khong_kiem_duoc():
    result = RuleEngine().check(DocumentStructure(blocks=[], source_format="docx"))

    assert result.status == "skipped" and "không đọc được" in result.reason.lower()


def test_rule_doc_tu_yaml_khong_hardcode():
    rules = load_rules()
    assert rules["consistency"]["min_dominance"] == 0.6
    assert rules["page"]["margin_mm"]["min"] == 10
    # Không còn tiêu chí nào ép tài liệu theo một mẫu văn bản cụ thể.
    assert "required_components" not in rules and "document_types" not in rules


def test_tieu_chi_tat_bang_cach_bo_khoa_trong_yaml():
    """Bỏ một khoá trong YAML là tắt hẳn tiêu chí đó, không phải sửa code."""
    engine = RuleEngine(rules={"structure": {}, "consistency": {}, "page": {}}, name="rong")
    result = engine.check(DocumentStructure(
        blocks=[Block(id="P00", text="Đoạn văn không có tiêu đề nào ở trên.")],
        source_format="text",
    ))

    assert result.findings == [] and result.passed == []
    # Cũng không báo "chưa kiểm được" những tiêu chí mà bộ này vốn không có.
    assert result.skipped == []


# ------------------------------------------- chính tả: lớp tất định ------- #
# Lớp này thay LLM ở phần chính tả. Tiêu chí nhận việc: cái gì nó báo thì phải
# đúng - bỏ sót còn chữa được bằng cách thêm một dòng vào YAML, chứ báo oan thì
# người dùng mất lòng tin vào cả bản soát.

def _typo(*texts):
    return soat_chinh_ta([Block(id=f"P{i:02}", text=t) for i, t in enumerate(texts)])


def test_so_lieu_viet_dung_khong_bi_bao():
    """Chỗ dễ báo oan nhất: dấu phẩy thập phân, giờ giấc, ngày tháng."""
    assert _typo("Chi phí 1,5 triệu đồng, họp lúc 12:30 ngày 5/9/2026.") == []


def test_dinh_sau_ngay_thang_van_bi_bao():
    """Nhưng "2026,kèm" thì chữ số bên trái mà CHỮ bên phải - dính thật."""
    found = _typo("Báo cáo trước ngày 20/9/2026,kèm số liệu.")

    assert len(found) == 1 and found[0].type == "punctuation"


def test_nua_ngoac_cua_so_thu_tu_khong_phai_ngoac_le(tmp_path):
    """"a)" "b)" "1)" là cách đánh số chuẩn, không phải ngoặc đơn thiếu vế mở."""
    assert _typo("a) Rà soát nhân sự;") == []
    assert _typo("Nội dung gồm: 1) rà soát; 2) thống kê; 3) báo cáo.") == []
    # Ngoặc thật vẫn phải đóng.
    assert [f.type for f in _typo("Cử cán bộ (ghi rõ họ tên.")] == ["punctuation"]
    # Và ")" của một ngoặc đang mở thì đóng đúng ngoặc đó, không bị nhầm là số thứ tự.
    assert _typo("Chi tiết (xem mục 1) đã nêu ở trên.") == []


def test_tu_lay_doi_viet_roi_khong_phai_lap_tu():
    assert _typo("Đơn vị luôn luôn chủ động triển khai.") == []
    assert [f.type for f in _typo("Tổng hợp các các đề xuất.")] == ["duplicate"]


def test_khoi_bang_khong_bi_do_dau_cach():
    """Ô bảng căn chỉnh bằng dấu cách, và hai ô trùng nội dung là chuyện thường."""
    bang = Block(id="T00", kind="table", text="Chức danh    Mức lương\nNhân viên    Nhân viên")

    assert soat_chinh_ta([bang]) == []


def test_van_ban_soan_dung_thi_khong_co_loi_nao():
    """Van chặn quan trọng nhất: văn bản sạch phải ra kết quả sạch."""
    assert _typo(
        "Căn cứ Kế hoạch công tác năm 2026 của Ban Giám đốc;",
        "Các đơn vị nghiêm túc triển khai, bổ sung nhân sự và sử dụng hiệu quả "
        "trang thiết bị được giao.",
        "Báo cáo gửi về Phòng Hành chính quản trị trước ngày 20/9/2026.",
    ) == []


def test_thieu_file_cau_hinh_thi_bo_qua_chu_khong_vo(tmp_path):
    """Mất YAML thì phần chính tả im lặng, không kéo cả bản soát xuống."""
    assert soat_chinh_ta([Block(id="P00", text="bổ xung")],
                         rules=load_chinh_ta(str(tmp_path / "khong-co.yaml"))) == []


# ------------------------------------ chính tả: LLM soát, có phép thử tách rời - #
# Bảng cặp sai->đúng dò bằng từ điển đã bị bỏ: nó không kết luận được một mình.
# Tiếng Việt viết rời từng âm tiết nên hai TỪ ĐÚNG đứng cạnh nhau trông y hệt một
# từ viết sai - "Đơn vị CŨNG CỐ gắng hoàn thành", "HỒ SƠ XUẤT khẩu đã duyệt".
# Nay LLM soát chính tả, và prompt bắt nó tự làm phép thử tách rời rồi KHAI ra;
# `loc_tach_roi` thi hành lời khai đó. Các test dưới canh đúng cái van ấy.

def test_bo_loi_ma_model_khai_la_tach_roi_van_xuoi():
    giu, bo = doc_nodes.loc_tach_roi([
        {"quote": "cũng cố", "suggest": "củng cố", "van_xuoi": True,
         "tach_roi": "cũng | cố gắng → đọc xuôi"},
        {"quote": "bổ xung", "suggest": "bổ sung", "van_xuoi": False,
         "tach_roi": "bổ | xung → vô nghĩa"},
    ])

    assert bo == 1
    assert [f["quote"] for f in giu] == ["bổ xung"]


def test_loi_giu_lai_duoc_gan_dung_loai_va_muc_do():
    """Nhánh này chỉ sinh lỗi chính tả, và chính tả sai là sai - luôn mức error."""
    giu, _ = doc_nodes.loc_tach_roi([{"quote": "sữa chữa", "suggest": "sửa chữa"}])

    assert giu[0]["type"] == "spelling" and giu[0]["severity"] == "error"


def test_thieu_truong_van_xuoi_thi_van_giu():
    """Model quên khai thì đừng nuốt lỗi - còn có van trích dẫn chặn phía sau."""
    giu, bo = doc_nodes.loc_tach_roi([{"quote": "nghành", "suggest": "ngành"}])

    assert bo == 0 and len(giu) == 1


class SpellLLM:
    """LLM giả cho nhánh chính tả; `tra` là JSON model trả về cho mỗi lô."""

    def __init__(self, tra=None, hong=False) -> None:
        self.tra = tra if tra is not None else {"findings": []}
        self.hong = hong
        self.messages = None

    async def chat_json(self, messages, **kwargs):
        self.messages = messages
        if self.hong:
            raise RuntimeError("vLLM không phản hồi")
        return self.tra


def _state_chinh_ta(text: str):
    structure = DocumentStructure(blocks=[Block(id="P00", text=text)], source_format="text")
    return {"outline": build_outline(structure), "structure": structure}


async def test_nhanh_chinh_ta_giu_loi_trich_dan_duoc_kem_vi_tri(monkeypatch):
    cau = "Đơn vị đã bổ xung nhân sự trong tháng 9."
    llm = SpellLLM({"findings": [
        {"block_id": "P00", "quote": "bổ xung", "suggest": "bổ sung",
         "van_xuoi": False, "message": "Sai chính tả"}]})
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)

    ra = await doc_nodes.chinh_ta_llm_node(_state_chinh_ta(cau))
    loi = ra["spell_findings"][0]

    assert loi["type"] == "spelling" and loi["source"] == "llm"
    assert cau[loi["start"]:loi["end"]] == "bổ xung"


async def test_nhanh_chinh_ta_bo_loi_bia_khong_trich_dan_duoc(monkeypatch):
    """Van chống ảo giác vẫn giữ nguyên: không trích được nguyên văn thì bỏ."""
    llm = SpellLLM({"findings": [
        {"block_id": "P00", "quote": "câu hoàn toàn không có trong văn bản",
         "suggest": "x", "van_xuoi": False}]})
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)

    ra = await doc_nodes.chinh_ta_llm_node(_state_chinh_ta("Đơn vị đã hoàn thành."))

    assert ra["spell_findings"] == []


async def test_nhanh_chinh_ta_bo_loi_tach_roi_van_xuoi(monkeypatch):
    """"Đơn vị cũng cố gắng..." là câu ĐÚNG - model khai xuôi thì phải biến mất."""
    llm = SpellLLM({"findings": [
        {"block_id": "P00", "quote": "cũng cố", "suggest": "củng cố",
         "tach_roi": "cũng | cố gắng", "van_xuoi": True}]})
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)

    ra = await doc_nodes.chinh_ta_llm_node(
        _state_chinh_ta("Đơn vị cũng cố gắng hoàn thành nhiệm vụ."))

    assert ra["spell_findings"] == []
    assert ra["trace"]["spell_bo_tach_roi"] == 1


async def test_mat_llm_thi_mat_phan_chinh_ta_chu_khong_vo_ca_ban_soat(monkeypatch):
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: SpellLLM(hong=True))

    ra = await doc_nodes.chinh_ta_llm_node(_state_chinh_ta("Đơn vị đã bổ xung nhân sự."))

    assert ra["spell_findings"] == []


async def test_prompt_chinh_ta_mang_theo_bay_hai_tu_dung_canh_nhau(monkeypatch):
    """Phần dạy về bẫy chính là thứ quyết định có báo oan hay không - đừng để rơi."""
    llm = SpellLLM()
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)
    await doc_nodes.chinh_ta_llm_node(_state_chinh_ta("Đơn vị đã hoàn thành."))

    system = llm.messages[0]["content"]
    assert "TÁCH RỜI" in system
    assert "cũng cố gắng" in system and "hồ sơ xuất khẩu" in system.lower()
    assert "van_xuoi" in system
    # Biến thể được chấp nhận không được coi là lỗi.
    assert "qui/quy" in system
    # Nhánh này KHÔNG được lấn sang việc của bộ tất định.
    assert "dấu cách" in system


async def test_nhanh_chinh_ta_khong_cham_vao_dau_cach(monkeypatch):
    """Lỗi máy móc là việc của `chinh_ta_node`; hai nhánh báo trùng thì rối."""
    llm = SpellLLM()
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)
    state = _state_chinh_ta("Đơn vị đã  hoàn thành.")
    ra = await doc_nodes.chinh_ta_llm_node(state)

    assert ra["spell_findings"] == []
    # Còn bộ tất định thì vẫn phải bắt được.
    assert [f.type for f in soat_chinh_ta(state["structure"].blocks)] == ["spacing"]


def test_hai_nhanh_chi_cung_mot_cho_thi_giu_cai_hep_hon():
    """Nhánh chính tả báo "Công căn", nhánh chữ nghĩa báo "khoản 2 Công căn này".

    Cùng một lỗi. Hiện hai khung lồng nhau thì người đọc tưởng hai lỗi, mà khung
    rộng còn ôm cả chữ viết đúng vào trong.
    """
    hep = {"block_id": "P01", "start": 8, "end": 16, "quote": "Công căn", "source": "llm"}
    rong = {"block_id": "P01", "start": 0, "end": 20, "quote": "khoản 2 Công căn này",
            "source": "llm"}

    gop = doc_nodes.gop_phat_hien([], [rong, hep], ["P01"])

    assert [f["quote"] for f in gop] == ["Công căn"]


def test_hai_cho_khac_nhau_trong_cung_khoi_thi_giu_ca_hai():
    a = {"block_id": "P01", "start": 0, "end": 8, "quote": "Công căn", "source": "llm"}
    b = {"block_id": "P01", "start": 30, "end": 38, "quote": "bổ xung", "source": "llm"}

    assert len(doc_nodes.gop_phat_hien([], [a, b], ["P01"])) == 2


def test_phat_hien_tat_dinh_thang_phat_hien_llm_cung_cho():
    """Đo được bằng vị trí ký tự thì chắc hơn phán đoán - LLM nhường."""
    tat_dinh = {"block_id": "P01", "start": 5, "end": 7, "quote": "  ", "source": "rule"}
    llm = {"block_id": "P01", "start": 0, "end": 20, "quote": "cả câu", "source": "llm"}

    gop = doc_nodes.gop_phat_hien([tat_dinh], [llm], ["P01"])

    assert [f["source"] for f in gop] == ["rule"]


# ----------------------------------------------- LLM: xác minh trích dẫn -- #
def test_loai_bo_phat_hien_khong_trich_dan_duoc():
    blocks = [Block(id="P01", text="Đề nghị bổ xung 02 nhân sự cho phòng.")]
    raw = [
        {"block_id": "P01", "type": "spelling", "quote": "bổ xung", "suggest": "bổ sung",
         "severity": "error"},
        {"block_id": "P01", "type": "logic", "quote": "câu này không hề tồn tại",
         "suggest": "x", "severity": "error"},
    ]
    verified = doc_nodes.verify_findings(raw, blocks)

    assert len(verified) == 1 and verified[0]["quote"] == "bổ xung"


def test_sua_lai_block_id_khi_model_ghi_nham():
    blocks = [Block(id="P01", text="Đoạn một."), Block(id="P02", text="Hạn nộp là ngày 20/9.")]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P99", "type": "missing", "quote": "ngày 20/9", "severity": "warning",
          "message": "Thiếu tháng và năm"}],
        blocks,
    )
    assert verified[0]["block_id"] == "P02"


def test_chuan_hoa_loai_va_muc_do_la():
    blocks = [Block(id="P01", text="Nội dung mẫu.")]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "bịa_loại", "severity": "critical",
          "quote": "Nội dung mẫu", "message": "Nhận xét gì đó"}],
        blocks,
    )
    assert verified[0]["type"] == "wording" and verified[0]["severity"] == "warning"


def test_bo_phat_hien_trich_ca_doan():
    """Kết quả dùng để KHOANH VÙNG trên trang. Trích cả đoạn thì khung ôm trọn
    đoạn đó, người đọc nhìn vào vẫn không biết chữ nào sai - bằng lúc chưa soát.
    Model để mặc thì rất hay chép cả đoạn rồi "gợi ý" viết lại nguyên đoạn."""
    doan = "Đơn vị đã hoàn thành nhiệm vụ được giao. " * 12
    blocks = [Block(id="P01", text=doan)]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "grammar", "quote": doan.strip(),
          "suggest": doan.strip(), "message": "Viết lại cả đoạn"}],
        blocks,
    )

    assert verified == []


def test_giu_phat_hien_trich_ngan_dung_cho():
    blocks = [Block(id="P01", text="Thực hiện trình tự thủ tục thực hiện xét duyệt.")]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "wording", "quote": "trình tự thủ tục",
          "suggest": "trình tự, thủ tục", "message": "Thiếu dấu phẩy giữa hai danh từ"}],
        blocks,
    )

    assert len(verified) == 1
    assert blocks[0].text[verified[0]["start"]:verified[0]["end"]] == "trình tự thủ tục"


def test_bo_phat_hien_chi_doi_khoang_trang():
    """Gặp liên tục trên PDF: khâu đọc file làm mất dấu xuống dòng, model tưởng
    câu chạy liền rồi "sửa" bằng cách thêm xuống dòng. Đó là vá lỗi của máy đọc
    file, không phải lỗi trong văn bản gốc."""
    text = "b) Danh sách đính kèm, gồm: (1) Mẫu số 06; (2) Mẫu số 07;"
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "grammar", "quote": text,
          "suggest": "b) Danh sách đính kèm, gồm:\n(1) Mẫu số 06;\n(2) Mẫu số 07;",
          "message": "Nên tách dòng"}],
        [Block(id="P01", text=text)],
    )

    assert verified == []


def test_bo_phat_hien_khong_noi_duoc_gi():
    """Rác của khâu đọc file ("1516 11" ở chân trang) hay ra dạng này: có trích
    dẫn, nhưng không nói được sai ở đâu mà cũng không đưa được cách sửa."""
    blocks = [Block(id="P01", text="1516 11")]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "missing", "quote": "1516 11",
          "suggest": "", "message": ""}],
        blocks,
    )

    assert verified == []


def test_chia_lo_theo_kich_thuoc():
    blocks = [Block(id=f"P{i:02d}", text="x" * 1000) for i in range(5)]
    batches = doc_nodes.build_review_batches(blocks, max_chars=2400)

    assert len(batches) > 1
    assert all(sum(len(b.text) for b in batch) <= 3000 for batch in batches)
    assert sum(len(batch) for batch in batches) == 5     # không mất khối nào


# --------------------------------------------------- toàn bộ workflow 2 --- #
DEPARTMENTS = [
    {"ma_phong_ban": "P.TCCB", "ten_phong_ban": "Phòng Tổ chức cán bộ",
     "mo_ta": "Quản lý nhân sự, biên chế, tuyển dụng."},
    {"ma_phong_ban": "P.HCQT", "ten_phong_ban": "Phòng Hành chính quản trị",
     "mo_ta": "Văn thư, lưu trữ, quản lý trang thiết bị, tổng hợp báo cáo."},
]


class FakeLLM:
    """Trả lời theo system prompt nhận được, mô phỏng cả trường hợp model bịa."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        # Lỗi chính tả nhánh LLM trả về; mặc định không có.
        self.spell: list[dict] = []

    async def chat_json(self, messages, **kwargs):
        system = messages[0]["content"]
        self.calls.append(system[:40])

        if "rà soát CHỮ NGHĨA" in system:
            return {"findings": [
                {"block_id": "P00", "type": "wording", "quote": "rà soát nhân sự",
                 "suggest": "rà soát lại nhân sự", "severity": "warning", "message": "ví dụ"},
                {"block_id": "P00", "type": "logic", "quote": "câu hoàn toàn bịa",
                 "suggest": "x", "severity": "error"},
            ]}
        if "CỔNG GÁC" in system:
            import re as _re
            cum = _re.findall(r'Cụm bị nghi: "([^"]+)"', messages[1]["content"])
            return {"ket_qua": [{"id": i + 1, "mot_tu": self.gate.get(c, True)}
                                for i, c in enumerate(cum)]}
        if "phân loại tài liệu" in system:
            return {"document_type": "cong_van_den", "topic": "trang_thiet_bi",
                    "confidence": 0.91, "reason": "Yêu cầu tổng hợp báo cáo trang thiết bị."}
        return {
            "summary": "Ban Giám đốc yêu cầu các đơn vị báo cáo nhân sự và trang thiết bị.",
            "deadline": "20/09/2026",
            "tasks": [
                {"department": "P.TCCB", "task": "Rà soát nhân sự đến 30/9/2026",
                 "data_needed": ["nhân sự", "ngày kiểm kê"], "deadline": "20/09/2026"},
                {"department": "", "department_name": "Ban Giám đốc",
                 "task": "Tiếp nhận báo cáo tổng hợp", "data_needed": []},
                {"department": "P.BIA", "department_name": "Phòng Bịa",
                 "task": "Mã phòng ban model tự nghĩ ra", "data_needed": []},
                {"department": "", "department_name": "",
                 "task": "Không rõ ai phải làm", "data_needed": []},
            ],
        }


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(doc_nodes, "get_llm", lambda: llm)
    return llm


async def test_workflow_chay_ca_bon_nhanh(cong_van, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(
        file_path=str(cong_van), file_name="CV-105.md",
        noi_gui="Ban Giám đốc", departments=DEPARTMENTS,
    )

    assert result["document"]["source_format"] == "text"
    assert result["rule_check"]["status"] == "partial"     # .md không có định dạng
    assert result["classification"]["document_type"] == "cong_van_den"
    assert result["summary"].startswith("Ban Giám đốc")
    assert result["tasks"][0]["department_name"] == "Phòng Tổ chức cán bộ"
    assert {"llm_review", "totals"} <= result.keys()


async def test_dan_y_duoc_tra_ve_cho_giao_dien(huong_dan, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(huong_dan), departments=[])
    dan_y = result["document"]["outline"]

    assert result["document"]["title"] == "Hướng dẫn vận hành hệ thống"
    assert [m["text"] for m in dan_y][:2] == ["Hướng dẫn vận hành hệ thống", "1. Mục đích"]
    assert all({"block_id", "level", "text"} == set(m) for m in dan_y)


async def test_noi_nhan_ngoai_danh_muc_van_duoc_giu(cong_van, fake_llm):
    """Ban Giám đốc không phải một dòng trong bảng phòng ban, nhưng văn bản giao
    việc cho họ thì người đọc vẫn phải thấy - trước đây bị lọc bỏ im lặng."""
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=DEPARTMENTS)
    ten = [t["department_name"] for t in result["tasks"]]

    assert "Ban Giám đốc" in ten
    ngoai = next(t for t in result["tasks"] if t["department_name"] == "Ban Giám đốc")
    assert ngoai["in_catalog"] is False
    assert ngoai["department"] == ""       # không có mã thì không giả vờ có


async def test_ma_phong_ban_model_tu_nghi_ra_bi_ha_xuong_ngoai_danh_muc(cong_van, fake_llm):
    """Mã bịa thì bỏ mã, nhưng vẫn giữ tên để không nuốt mất một nơi nhận."""
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=DEPARTMENTS)

    assert "P.BIA" not in [t["department"] for t in result["tasks"]]
    bia = next(t for t in result["tasks"] if t["department_name"] == "Phòng Bịa")
    assert bia["department"] == "" and bia["in_catalog"] is False


async def test_muc_khong_xac_dinh_duoc_noi_nhan_thi_bo(cong_van, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=DEPARTMENTS)

    assert all(t["department_name"] for t in result["tasks"])
    assert "Không rõ ai phải làm" not in [t["task"] for t in result["tasks"]]


async def test_loi_bia_bi_loai_va_lo_theo_khoi(cong_van, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=DEPARTMENTS)
    review = result["llm_review"]
    quotes = [f["quote"] for group in review.values() for f in group]

    assert "câu hoàn toàn bịa" not in quotes
    assert "rà soát nhân sự" in quotes
    # Gom theo khối: khoá là mã khối, và mọi lỗi trong nhóm đều thuộc khối đó.
    assert all(all(f["block_id"] == key for f in group) for key, group in review.items())


async def test_khong_co_danh_muc_van_phan_loai_duoc(cong_van, fake_llm):
    """Phân loại chỉ đọc nội dung tài liệu nên không cần danh mục phòng ban."""
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=[])

    assert result["classification"]["document_type"] == "cong_van_den"
    # Không có danh mục thì mọi nơi nhận đều là ngoài danh mục, nhưng không mất.
    assert [t["department_name"] for t in result["tasks"]] == [
        "P.TCCB", "Ban Giám đốc", "Phòng Bịa"]


async def test_file_hong_khong_lam_vo_workflow(tmp_path, fake_llm):
    from app.agents.graph import run_document_workflow

    path = tmp_path / "hong.docx"
    path.write_bytes(b"day khong phai docx")
    result = await run_document_workflow(file_path=str(path), departments=DEPARTMENTS)

    assert result["error"]
    assert result["rule_check"]["status"] == "skipped"


async def test_tai_lieu_bat_ky_van_ra_ket_qua(tmp_path, fake_llm):
    """Tài liệu kỹ thuật không có thành phần thể thức nào - vẫn phải soát được."""
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(
        file_path=str(_docx_noi_dung(tmp_path, lech_phong=True, lech_co=True)),
        departments=DEPARTMENTS,
    )

    rules = {f["rule"] for f in result["rule_check"]["findings"]}
    assert result["rule_check"]["status"] != "skipped"
    assert "consistency.font" in rules
    assert result["totals"]["warnings"] > 0
