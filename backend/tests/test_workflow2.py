"""Workflow 2: parse có định dạng, rule engine, và ba nhánh LLM (dùng LLM giả)."""

from __future__ import annotations

import pytest

from app.agents.nodes import document as doc_nodes
from app.documents.parser import Block, DocumentStructure, PageGeometry, parse_document
from app.documents.rules import RuleEngine, load_rules
from app.documents.structure import body_blocks, detect_components

CONG_VAN = """CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc

CÔNG TY TPV
BAN GIÁM ĐỐC

Số: 105/CV-BGĐ

Hà Nội, ngày 05 tháng 9 năm 2026

CÔNG VĂN
V/v tổng hợp, báo cáo tình trạng trang thiết bị

Kính gửi: Các phòng ban, đơn vị trực thuộc

Ban Giám đốc yêu cầu các đơn vị rà soát quân số tính đến ngày 30/9/2026.

Báo cáo gửi về Phòng Hành chính quản trị trước ngày 20/9/2026.

Nơi nhận:
- Như trên;
- Lưu: VT.

GIÁM ĐỐC
"""


@pytest.fixture
def cong_van(tmp_path):
    path = tmp_path / "CV-105.md"
    path.write_text(CONG_VAN, encoding="utf-8")
    return path


@pytest.fixture
def docx_sai_the_thuc(tmp_path):
    """DOCX cố tình sai: phông Arial, cỡ 11, thiếu hầu hết thành phần thể thức.

    Vẫn giữ quốc hiệu + tiêu ngữ vì đây phải là một văn bản hành chính THIẾU SÓT.
    Không có dấu hiệu nào thì rule engine coi tệp nằm ngoài phạm vi và bỏ qua -
    đúng chủ ý, nhưng không phải thứ test này muốn đo.
    """
    docx = pytest.importorskip("docx")
    from docx.shared import Pt

    path = tmp_path / "ban_thao.docx"
    document = docx.Document()
    p1 = document.add_paragraph("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    p1.runs[0].font.name = "Times New Roman"
    p1.runs[0].font.size = Pt(13)
    p_tieu_ngu = document.add_paragraph("Độc lập - Tự do - Hạnh phúc")
    p_tieu_ngu.runs[0].font.name = "Times New Roman"
    p_tieu_ngu.runs[0].font.size = Pt(13)
    p2 = document.add_paragraph("Đoạn nội dung đặt sai phông và cỡ chữ.")
    p2.runs[0].font.name = "Arial"
    p2.runs[0].font.size = Pt(11)
    document.save(path)
    return path


# ---------------------------------------------------------------- parser --- #
def test_parse_docx_lay_duoc_phong_co_chu_va_le(docx_sai_the_thuc):
    structure = parse_document(docx_sai_the_thuc)

    assert structure.source_format == "docx"
    assert structure.has_format_info is True
    # blocks[0] quốc hiệu, [1] tiêu ngữ, [2] đoạn nội dung đặt sai
    assert structure.blocks[2].font == "Arial"
    assert structure.blocks[2].size_pt == 11.0
    assert structure.geometry is not None and structure.geometry.measured is False


def test_parse_text_khong_co_thong_tin_dinh_dang(cong_van):
    structure = parse_document(cong_van)
    assert structure.source_format == "text"
    assert structure.has_format_info is False


def test_dinh_dang_khong_ho_tro(tmp_path):
    path = tmp_path / "a.pptx"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="chỉ xử lý"):
        parse_document(path)


# ------------------------------------------------------ dò thành phần ----- #
def test_do_du_thanh_phan_the_thuc(cong_van):
    components = detect_components(parse_document(cong_van))

    assert components.has("quoc_hieu") and components.has("tieu_ngu")
    assert components.value_of("so_ky_hieu") == "105/CV-BGĐ"
    assert components.value_of("ten_loai").upper() == "CÔNG VĂN"
    assert "trang thiết bị" in components.value_of("trich_yeu")
    assert components.has("noi_nhan") and components.has("chu_ky")


def test_quoc_hieu_chap_nhan_ca_hai_cach_bo_dau(tmp_path):
    for variant in ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "CỘNG HOÀ XÃ HỘI CHỦ NGHĨA VIỆT NAM"):
        path = tmp_path / "vb.md"
        path.write_text(variant, encoding="utf-8")
        assert detect_components(parse_document(path)).has("quoc_hieu")


def test_khoi_noi_dung_bo_phan_the_thuc(cong_van):
    structure = parse_document(cong_van)
    blocks = body_blocks(structure, detect_components(structure))
    joined = " ".join(b.text for b in blocks)

    assert "CỘNG HÒA" not in joined          # không soát chính tả quốc hiệu
    assert "Nơi nhận" not in joined
    assert "rà soát quân số" in joined


# ----------------------------------------------------------- rule engine -- #
def test_bat_dung_loi_phong_va_co_chu(docx_sai_the_thuc):
    structure = parse_document(docx_sai_the_thuc)
    result = RuleEngine().check(structure, detect_components(structure))

    font_findings = [f for f in result.findings if f.rule == "format.font"]
    assert len(font_findings) == 1
    assert font_findings[0].actual == "Arial" and font_findings[0].block_id == "P02"

    size_findings = [f for f in result.findings if f.rule == "format.size_pt"]
    assert any(f.actual == "11pt" for f in size_findings)


def test_thanh_phan_thieu_duoc_bao_loi(docx_sai_the_thuc):
    structure = parse_document(docx_sai_the_thuc)
    result = RuleEngine().check(structure, detect_components(structure))
    thieu = {f.rule for f in result.findings if f.rule.startswith("required.")}

    assert "required.so_ky_hieu" in thieu
    assert "required.chu_ky" in thieu
    assert "required.quoc_hieu" not in thieu     # có quốc hiệu nên không báo


def test_van_ban_scan_thi_bo_qua_the_thuc_chu_khong_bao_dat():
    scan = DocumentStructure(
        blocks=[Block(id="P00", text="CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")],
        source_format="pdf_scan",
    )
    result = RuleEngine().check(scan, detect_components(scan), enforce_scope=False)

    assert result.status == "partial"            # KHÔNG phải "done"
    assert "format.font" in result.skipped
    assert "scan" in result.reason.lower()


def test_le_pdf_chi_kiem_tra_phia_dang_tin():
    """Lề PDF suy từ vùng chữ: lề dưới/phải của trang ngắn là vô nghĩa."""
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=22, bottom_mm=200,
                              left_mm=32, right_mm=90, measured=True),
        source_format="pdf_digital",
    )
    result = RuleEngine().check(structure, detect_components(structure), enforce_scope=False)
    margin_rules = {f.rule for f in result.findings if "margin" in f.rule}

    assert margin_rules == set()                              # top/left đều hợp lệ
    assert any("margin_mm.bottom" in s for s in result.skipped)
    assert any("margin_mm.right" in s for s in result.skipped)


def test_so_ky_hieu_sai_dang_bi_bao(tmp_path):
    path = tmp_path / "vb.md"
    path.write_text("Số: 105\n\nNội dung.", encoding="utf-8")
    structure = parse_document(path)
    result = RuleEngine().check(structure, detect_components(structure), enforce_scope=False)

    assert any(f.rule == "required.so_ky_hieu.format" for f in result.findings)


def test_rule_doc_tu_yaml_khong_hardcode():
    rules = load_rules()
    assert rules["format"]["font"]["allowed"] == ["Times New Roman"]
    assert rules["format"]["size_pt"]["min"] == 13


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
        [{"block_id": "P99", "type": "missing", "quote": "ngày 20/9", "severity": "warning"}],
        blocks,
    )
    assert verified[0]["block_id"] == "P02"


def test_chuan_hoa_loai_va_muc_do_la():
    blocks = [Block(id="P01", text="Nội dung mẫu.")]
    verified = doc_nodes.verify_findings(
        [{"block_id": "P01", "type": "bịa_loại", "severity": "critical",
          "quote": "Nội dung mẫu"}],
        blocks,
    )
    assert verified[0]["type"] == "wording" and verified[0]["severity"] == "warning"


def test_chia_lo_theo_kich_thuoc():
    blocks = [Block(id=f"P{i:02d}", text="x" * 1000) for i in range(5)]
    batches = doc_nodes.build_review_batches(blocks, max_chars=2400)

    assert len(batches) > 1
    assert all(sum(len(b.text) for b in batch) <= 3000 for batch in batches)
    assert sum(len(batch) for batch in batches) == 5     # không mất khối nào


# --------------------------------------------------- toàn bộ workflow 2 --- #
DEPARTMENTS = [
    {"ma_phong_ban": "P.TCCB", "ten_phong_ban": "Phòng Tổ chức cán bộ",
     "mo_ta": "Quản lý nhân sự, biên chế, quân số."},
    {"ma_phong_ban": "P.HCQT", "ten_phong_ban": "Phòng Hành chính quản trị",
     "mo_ta": "Văn thư, lưu trữ, quản lý trang thiết bị, tổng hợp báo cáo."},
]


class FakeLLM:
    """Trả lời theo system prompt nhận được, mô phỏng cả trường hợp model bịa."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat_json(self, messages, **kwargs):
        system = messages[0]["content"]
        self.calls.append(system[:40])

        if "rà soát bản thảo" in system:
            return {"findings": [
                {"block_id": "P00", "type": "spelling", "quote": "rà soát quân số",
                 "suggest": "rà soát quân số", "severity": "warning", "message": "ví dụ"},
                {"block_id": "P00", "type": "logic", "quote": "câu hoàn toàn bịa",
                 "suggest": "x", "severity": "error"},
            ]}
        if "phân loại văn bản đến" in system:
            return {"document_type": "cong_van_den", "topic": "trang_thiet_bi",
                    "confidence": 0.91, "reason": "Yêu cầu tổng hợp báo cáo trang thiết bị."}
        return {
            "summary": "Ban Giám đốc yêu cầu các đơn vị báo cáo quân số và trang thiết bị.",
            "deadline": "20/09/2026",
            "tasks": [
                {"department": "P.TCCB", "task": "Rà soát quân số đến 30/9/2026",
                 "data_needed": ["quân số", "ngày kiểm kê"], "deadline": "20/09/2026"},
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


async def test_loi_bia_bi_loai_trong_ket_qua_cuoi(cong_van, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(file_path=str(cong_van), departments=DEPARTMENTS)
    quotes = [f["quote"] for f in result["llm_review"]["findings"]]

    assert "câu hoàn toàn bịa" not in quotes
    assert "rà soát quân số" in quotes


async def test_khong_co_danh_muc_van_phan_loai_duoc(cong_van, fake_llm):
    """Phân loại chỉ đọc nội dung văn bản nên không cần danh mục phòng ban."""
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


async def test_docx_sai_the_thuc_ra_dung_ket_qua_demo(docx_sai_the_thuc, fake_llm):
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(
        file_path=str(docx_sai_the_thuc), departments=DEPARTMENTS,
    )

    rules = {f["rule"] for f in result["rule_check"]["findings"]}
    assert "format.font" in rules                      # "Font đoạn ... không đúng"
    assert "required.noi_nhan" in rules                # "Thiếu nơi nhận"
    assert result["totals"]["errors"] > 0


# --------------------------------------- thể thức: canh lề, giãn dòng ----- #
def _docx(tmp_path, name="the_thuc.docx", *, align=None, line=1.15,
          before=3, after=3, quoc_hieu_trong_bang=False):
    """DOCX tối thiểu nhưng đủ thành phần để rule engine chạy tới các bước mới."""
    docx = pytest.importorskip("docx")
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    document = docx.Document()

    def dat(paragraph, *, size=13, bold=False, canh=None):
        run = paragraph.runs[0]
        run.font.name, run.font.size, run.font.bold = "Times New Roman", Pt(size), bold
        pf = paragraph.paragraph_format
        pf.line_spacing, pf.space_before, pf.space_after = line, Pt(before), Pt(after)
        if canh is not None:
            paragraph.alignment = canh
        return paragraph

    if quoc_hieu_trong_bang:
        # Bố cục hai cột quen thuộc: tên cơ quan bên trái, quốc hiệu bên phải.
        table = document.add_table(rows=1, cols=2)
        trai = table.cell(0, 0).paragraphs[0]
        trai.add_run("CÔNG TY TNHH ABC")
        dat(trai, size=10, bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)
        phai = table.cell(0, 1).paragraphs[0]
        phai.add_run("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
        dat(phai, size=12, bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)
        tieu = table.cell(0, 1).add_paragraph()
        tieu.add_run("Độc lập - Tự do - Hạnh phúc")
        dat(tieu, size=13, bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)
    else:
        dat(document.add_paragraph("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"),
            size=12, bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)
        dat(document.add_paragraph("Độc lập - Tự do - Hạnh phúc"),
            size=13, bold=True, canh=WD_ALIGN_PARAGRAPH.CENTER)

    dat(document.add_paragraph("Số: 105/CV-BGĐ"))
    dat(document.add_paragraph("Hà Nội, ngày 05 tháng 9 năm 2026"))
    dat(document.add_paragraph("Kính gửi: Các phòng ban"))
    dat(document.add_paragraph(
        "Đây là một đoạn nội dung đủ dài để không bị nhầm thành tiêu đề mục, "
        "nhằm kiểm tra các tiêu chí canh lề và khoảng cách đoạn của phần nội dung."),
        canh=align if align is not None else WD_ALIGN_PARAGRAPH.JUSTIFY)
    dat(document.add_paragraph("Nơi nhận:"))
    dat(document.add_paragraph("GIÁM ĐỐC"))

    path = tmp_path / name
    document.save(path)
    return path


def _check(path):
    structure = parse_document(path)
    return structure, RuleEngine().check(structure, detect_components(structure), enforce_scope=False)


def test_noi_dung_canh_deu_thi_dat(tmp_path):
    _, result = _check(_docx(tmp_path))

    assert "format.alignment" in result.passed
    assert "format.line_spacing" in result.passed
    assert "format.paragraph_spacing_pt" in result.passed


def test_noi_dung_canh_trai_bi_bao(tmp_path):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    _, result = _check(_docx(tmp_path, align=WD_ALIGN_PARAGRAPH.LEFT))
    sai = [f for f in result.findings if f.rule == "format.alignment"]

    assert sai and sai[0].actual == "LEFT" and sai[0].expected == "JUSTIFY"


def test_gian_dong_don_van_hop_le(tmp_path):
    """Quy định cho phép "dòng đơn HOẶC tối thiểu 1.15" - 1.0 không phải là lỗi."""
    _, result = _check(_docx(tmp_path, line=1.0))

    assert "format.line_spacing" in result.passed


def test_gian_dong_giua_hai_moc_bi_bao(tmp_path):
    _, result = _check(_docx(tmp_path, line=1.08))
    sai = [f for f in result.findings if f.rule == "format.line_spacing"]

    assert sai and sai[0].actual == "1.08"     # làm tròn từ 1.07917 mà docx lưu


def test_cach_doan_ngoai_khoang_bi_bao(tmp_path):
    _, result = _check(_docx(tmp_path, before=10, after=10))
    sai = [f for f in result.findings if f.rule == "format.paragraph_spacing_pt"]

    assert sai and sai[0].expected == "3-6pt"


def test_gian_dong_ke_thua_tu_style_van_kiem_duoc(tmp_path):
    """Đặt ở style là cách soạn đúng; không lần theo thì văn bản tử tế lại bị bỏ qua."""
    docx = pytest.importorskip("docx")
    from docx.shared import Pt

    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Times New Roman", Pt(13)
    style.paragraph_format.line_spacing = 1.5
    document.add_paragraph("Đoạn nội dung thừa hưởng giãn dòng từ style Normal.")
    path = tmp_path / "ke_thua.docx"
    document.save(path)

    structure = parse_document(path)

    assert structure.blocks[0].line_spacing == 1.5


# ------------------------- định dạng quốc hiệu / tiêu ngữ ----------------- #
def test_quoc_hieu_tieu_ngu_dung_dinh_dang_thi_dat(tmp_path):
    _, result = _check(_docx(tmp_path))

    assert "component_format.quoc_hieu" in result.passed
    assert "component_format.tieu_ngu" in result.passed


def test_quoc_hieu_sai_co_chu_va_do_dam_bi_bao(tmp_path):
    docx = pytest.importorskip("docx")
    from docx.shared import Pt

    document = docx.Document()
    p = document.add_paragraph("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    p.runs[0].font.name, p.runs[0].font.size, p.runs[0].font.bold = "Times New Roman", Pt(9), False
    path = tmp_path / "quoc_hieu_sai.docx"
    document.save(path)

    _, result = _check(path)
    sai = {f.rule for f in result.findings if f.rule.startswith("component_format.quoc_hieu")}

    assert "component_format.quoc_hieu.cỡ_chữ" in sai
    assert "component_format.quoc_hieu.độ_đậm" in sai


def test_quoc_hieu_trong_o_bang_van_doc_dung_dinh_dang(tmp_path):
    """Khối bảng là blob gộp mọi ô và lấy định dạng của ô ĐẦU (tên cơ quan, 10pt).

    Phán xét quốc hiệu trên blob đó thì báo sai hàng loạt - đây là bố cục phổ biến
    nhất của văn bản hành chính Việt Nam nên không được phép nhầm.
    """
    structure, result = _check(_docx(tmp_path, quoc_hieu_trong_bang=True))

    assert any(b.kind == "table" for b in structure.blocks)
    assert "component_format.quoc_hieu" in result.passed
    assert "component_format.tieu_ngu" in result.passed
    assert not [f for f in result.findings if f.rule.startswith("component_format.")]


def test_le_lech_vai_phan_nghin_mm_khong_bi_bao(tmp_path):
    """Word lưu lề theo inch: đặt đúng 1.5cm thì đọc về là 14.993mm.

    Không có dung sai thì mọi văn bản đặt lề chuẩn đều bị báo sai - đúng kiểu
    cảnh báo mà người dùng học cách phớt lờ, rồi phớt lờ luôn cảnh báo thật.
    """
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=20.0025,
                              bottom_mm=20.0025, left_mm=30.00375, right_mm=14.993),
        source_format="docx",
    )

    result = RuleEngine().check(structure, detect_components(structure), enforce_scope=False)

    assert not [f for f in result.findings if f.rule.startswith("format.margin_mm")]
    assert "format.margin_mm" in result.passed


def test_le_sai_that_van_bi_bao(tmp_path):
    """Dung sai không được nuốt lỗi thật: 10mm vẫn phải kêu."""
    structure = DocumentStructure(
        blocks=[Block(id="P00", text="Nội dung", font="Times New Roman", size_pt=13)],
        geometry=PageGeometry(width_mm=210, height_mm=297, top_mm=20, bottom_mm=20,
                              left_mm=30, right_mm=10.0),
        source_format="docx",
    )

    result = RuleEngine().check(structure, detect_components(structure), enforce_scope=False)
    sai = [f for f in result.findings if f.rule == "format.margin_mm.right"]

    assert sai and sai[0].actual == "10.0mm"


# ---------------------------------------- phạm vi & loại văn bản ---------- #
# Bộ tiêu chí NĐ 30 chỉ đúng với văn bản hành chính. Đem nó soi một tài liệu kỹ
# thuật thì ra vài chục lỗi đúng về máy móc mà vô nghĩa với người đọc - tệ hơn là
# lỗi thật chìm nghỉm trong đống đó.
def _md(tmp_path, text: str, name: str = "vb.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    structure = parse_document(path)
    return structure, detect_components(structure)


def test_tep_khong_phai_van_ban_hanh_chinh_thi_khong_ap_bo_tieu_chi(tmp_path):
    structure, comps = _md(tmp_path, "# Tài liệu đặc tả\n\nHệ thống gồm ba thành phần.\n")
    result = RuleEngine().check(structure, comps)

    assert result.status == "skipped"
    assert result.findings == []
    assert "văn bản hành chính" in result.reason


def test_van_ban_hanh_chinh_thieu_sot_thi_van_soat(tmp_path):
    """Đủ dấu hiệu là văn bản hành chính thì thiếu gì báo nấy, không bỏ qua."""
    structure, comps = _md(tmp_path, CONG_VAN)
    result = RuleEngine().check(structure, comps)

    assert result.status != "skipped"
    assert result.document_type == "cong_van"


def test_ep_soat_khi_nguoi_dung_biet_minh_lam_gi(tmp_path):
    structure, comps = _md(tmp_path, "# Tài liệu đặc tả\n\nNội dung.\n")
    result = RuleEngine().check(structure, comps, enforce_scope=False)

    assert result.status != "skipped"
    assert any(f.rule.startswith("required.") for f in result.findings)


def test_cong_van_khong_bi_doi_ten_loai(tmp_path):
    """Công văn là loại DUY NHẤT không ghi tên loại - đòi nó là báo lỗi oan."""
    structure, comps = _md(tmp_path, CONG_VAN)
    result = RuleEngine().check(structure, comps)

    assert "required.ten_loai" not in {f.rule for f in result.findings}


def test_bao_cao_duoc_doi_ten_loai_va_nhan_trich_yeu_khong_co_vv(tmp_path):
    bao_cao = CONG_VAN.replace("CÔNG VĂN\nV/v tổng hợp, báo cáo tình trạng trang thiết bị",
                               "BÁO CÁO\nVề tình hình trang thiết bị quý III")
    structure, comps = _md(tmp_path, bao_cao)
    result = RuleEngine().check(structure, comps)

    assert result.document_type == "bao_cao"
    # Trích yếu của văn bản có tên loại nằm ngay dưới tên loại, mở đầu bằng "Về".
    assert comps.has("trich_yeu")
    assert "required.trich_yeu" not in {f.rule for f in result.findings}


def test_bien_ban_khong_bi_doi_so_ky_hieu(tmp_path):
    bien_ban = (CONG_VAN.replace("Số: 105/CV-BGĐ\n\n", "")
                        .replace("CÔNG VĂN\nV/v tổng hợp, báo cáo tình trạng trang thiết bị",
                                 "BIÊN BẢN\nVề việc bàn giao trang thiết bị"))
    structure, comps = _md(tmp_path, bien_ban)
    result = RuleEngine().check(structure, comps)

    assert result.document_type == "bien_ban"
    assert "required.so_ky_hieu" not in {f.rule for f in result.findings}


def test_chuc_danh_ky_lay_tu_cau_hinh_chu_khong_chon_trong_code(tmp_path):
    """Cơ quan ký bằng chức danh lạ thì thêm vào YAML, không phải sửa regex."""
    vb = CONG_VAN.replace("GIÁM ĐỐC", "TRƯỞNG BAN QUẢN LÝ DỰ ÁN SỐ 7")
    path = tmp_path / "vb.md"
    path.write_text(vb, encoding="utf-8")
    structure = parse_document(path)

    mac_dinh = detect_components(structure)
    khai_them = detect_components(structure, chu_ky_titles=["TRƯỞNG BAN QUẢN LÝ DỰ ÁN SỐ 7"])

    assert not mac_dinh.has("chu_ky")     # danh sách mặc định không có chức danh này
    assert khai_them.has("chu_ky")


def test_chu_ky_nam_trong_o_bang_van_do_duoc(tmp_path):
    """Bố cục hai cột "Nơi nhận | chức danh" rất phổ biến; khối bảng gộp mọi ô
    bằng " | " nên mẫu neo đầu dòng trượt hết nếu không quét từng ô."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    document.add_paragraph("Số: 09/BC-KT")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).paragraphs[0].add_run("Nơi nhận:\n- Lưu: VT.")
    table.cell(0, 1).paragraphs[0].add_run("TỔNG GIÁM ĐỐC")
    path = tmp_path / "co_bang.docx"
    document.save(path)

    comps = detect_components(parse_document(path))
    assert comps.has("chu_ky")
