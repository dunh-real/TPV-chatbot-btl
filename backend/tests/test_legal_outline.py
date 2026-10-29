"""Dàn ý văn bản quy phạm theo văn phạm (legal_grammar) và chỗ nó nối vào outline/rule engine."""

from pathlib import Path

from app.documents.legal_grammar import parse_legal
from app.documents.outline import build_outline
from app.documents.parser import parse_document
from app.documents.rules import RuleEngine

LUAT = """NGHỊ ĐỊNH VỀ TUYỂN DỤNG

Chương I

QUY ĐỊNH CHUNG

Điều 1. Phạm vi điều chỉnh

Nghị định này quy định về tuyển dụng công chức.

Điều 2. Đối tượng áp dụng

1. Công chức quy định tại Luật Cán bộ, công chức.

2. Cơ quan quản lý công chức, bao gồm:

a) Bộ, cơ quan ngang Bộ;

b) Ủy ban nhân dân tỉnh.

3. Cơ quan sử dụng công chức.

Chương II

TUYỂN DỤNG

Mục 1. CĂN CỨ TUYỂN DỤNG

Điều 3. Căn cứ tuyển dụng công chức

1. Việc tuyển dụng phải căn cứ vào vị trí việc làm.

2. Cơ quan sử dụng công chức.
"""

SUA_DOI = """NGHỊ ĐỊNH SỬA ĐỔI

Điều 1. Sửa đổi, bổ sung một số điều của Nghị định số 71/2019/NĐ-CP

1. Bổ sung Điều 4a vào sau Điều 4 như sau:

“Điều 4a. Quy định về vi phạm đã kết thúc

1. Hành vi vi phạm đã kết thúc.

2. Hành vi vi phạm đang được thực hiện.”

2. Sửa đổi khoản 3 Điều 5 như sau:

“3. Phạt tiền từ 10.000.000 đồng.”

Điều 2. Hiệu lực thi hành

Nghị định này có hiệu lực từ ngày ký.
"""

BAO_CAO = """BÁO CÁO CÔNG TÁC

1. Tình hình chung

Đơn vị hoàn thành nhiệm vụ.

2. Kết quả thực hiện

Hoàn thành 12 hồ sơ.
"""


def _outline(tmp_path: Path, name: str, text: str):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    structure = parse_document(path)
    return structure, build_outline(structure)


def test_luat_dung_van_pham_va_cap_theo_loai_don_vi(tmp_path):
    _, outline = _outline(tmp_path, "luat.txt", LUAT)
    assert outline.method == "legal_grammar"
    assert not outline.explicit
    by_text = {h.text: h for h in outline.headings}
    # Tên Chương viết HOA ở dòng sau được nối vào tiêu đề.
    assert by_text["Chương I QUY ĐỊNH CHUNG"].level == 1
    assert by_text["Mục 1. CĂN CỨ TUYỂN DỤNG"].level == 2
    assert by_text["Điều 3. Căn cứ tuyển dụng công chức"].level == 3
    khoan = by_text["2. Cơ quan quản lý công chức, bao gồm:"]
    assert khoan.level == 4 and khoan.content_unit
    assert by_text["a) Bộ, cơ quan ngang Bộ;"].level == 5
    assert not by_text["Điều 1. Phạm vi điều chỉnh"].content_unit


def test_van_ban_sua_doi_long_noi_dung_trich_dan(tmp_path):
    _, outline = _outline(tmp_path, "sua_doi.txt", SUA_DOI)
    assert outline.method == "legal_grammar"
    levels = {h.text: h.level for h in outline.headings}
    assert levels["Điều 1. Sửa đổi, bổ sung một số điều của Nghị định số 71/2019/NĐ-CP"] == 1
    assert levels["1. Bổ sung Điều 4a vào sau Điều 4 như sau:"] == 2
    # Điều 4a là điều được chèn: nằm dưới lệnh sửa đổi, không ngang Điều 1, Điều 2.
    assert levels["“Điều 4a. Quy định về vi phạm đã kết thúc"] == 3
    assert levels["1. Hành vi vi phạm đã kết thúc."] == 4
    assert levels["Điều 2. Hiệu lực thi hành"] == 1


def test_tai_lieu_thuong_giu_cach_cu(tmp_path):
    _, outline = _outline(tmp_path, "bao_cao.txt", BAO_CAO)
    assert outline.method == "marker"
    assert [h.text for h in outline.headings] == ["1. Tình hình chung", "2. Kết quả thực hiện"]
    assert parse_legal(BAO_CAO.splitlines()) is None


def test_markdown_style_giu_cach_cu(tmp_path):
    _, outline = _outline(tmp_path, "hd.md", "# Hướng dẫn\n\n## Phần một\nNội dung.\n\n## Điều 1. Phần hai\nNội dung.\n")
    assert outline.method == "style" and outline.explicit


def test_khoan_diem_khong_bi_bao_muc_rong_hay_trung_ten(tmp_path):
    structure, _ = _outline(tmp_path, "luat.txt", LUAT)
    result = RuleEngine().check(structure)
    rules = {f.rule for f in result.findings}
    # "3. Cơ quan sử dụng công chức." đứng ngay trước Chương II, và "2. Cơ quan sử dụng
    # công chức." lặp lại ở Điều 3: là nội dung khoản, không phải mục rỗng hay trùng tên.
    assert "structure.empty_section" not in rules
    assert "structure.duplicate_heading" not in rules


def test_khoan_dai_van_bi_kiem_do_dai(tmp_path):
    dai = "1. " + "Nội dung khoản rất dài. " * 80
    text = LUAT.replace("1. Việc tuyển dụng phải căn cứ vào vị trí việc làm.", dai)
    structure, outline = _outline(tmp_path, "luat_dai.txt", text)
    assert outline.method == "legal_grammar"
    result = RuleEngine().check(structure)
    assert any(f.rule == "structure.paragraph_length" for f in result.findings)


def test_ket_qua_giai_ma_dung_lai_trong_mot_luot(tmp_path):
    structure, first = _outline(tmp_path, "luat.txt", LUAT)
    cached = structure.metadata["_legal_parse"][1]
    second = build_outline(structure)
    assert structure.metadata["_legal_parse"][1] is cached
    assert [h.text for h in first.headings] == [h.text for h in second.headings]


def test_dong_rac_trinh_duyet_bi_bo_qua():
    parsed = parse_legal(["10/7/26, 9:08 AM", "about:blank", "Điều 1. Phạm vi", "1. Khoản một.", "2/88"])
    assert parsed is not None
    assert parsed.labels[0] == "JUNK" and parsed.labels[4] == "JUNK"
    assert [h.kind for h in parsed.headings] == ["OWN_DIEU", "OWN_KHOAN"]


def test_phan_thu_bang_chu():
    parsed = parse_legal(["PHẦN THỨ NHẤT", "Điều 1. Phạm vi", "PHẦN THỨ HAI", "Điều 2. Hiệu lực"])
    assert [h.kind for h in parsed.headings] == ["OWN_PHAN", "OWN_DIEU", "OWN_PHAN", "OWN_DIEU"]


def test_dieu_dinh_lien_noi_dung_khong_bi_bao_muc_rong(tmp_path):
    # PDF hay gộp tên điều với câu nội dung thành một khối.
    text = ("Điều 1. Phạm vi điều chỉnh Nghị định này quy định về tuyển dụng công chức.\n\n"
            "Điều 2. Đối tượng áp dụng\n\n1. Công chức.\n\n2. Cơ quan quản lý công chức.\n")
    structure, outline = _outline(tmp_path, "dinh_lien.txt", text)
    assert outline.method == "legal_grammar"
    dieu1 = outline.headings[0]
    assert dieu1.text.startswith("Điều 1.") and dieu1.content_unit
    assert not outline.headings[1].content_unit
    rules = {f.rule for f in RuleEngine().check(structure).findings}
    assert "structure.empty_section" not in rules
