"""Câu hỏi thật của người dùng, chạy qua nguyên đường trích tham số.

VÌ SAO CÓ FILE NÀY

Ba lỗi nặng nhất của phần tạo slide đều lọt qua hơn 300 test:

  - `focus` model trả về cả câu thay vì một trong bốn giá trị -> ô chỉ tiêu hiện
    nhân sự dưới tiêu đề nói về thiết bị;
  - bảng 33 dòng bị cắt còn 8, kèm câu "xem chi tiết trong báo cáo" trỏ vào một
    bản báo cáo không tồn tại;
  - "tháng 8/2026 so với tháng 8 năm 2025" ra kỳ đối chiếu 2026-08, tức so kỳ với
    chính nó, rồi in "biến động 0 (0%)".

Cả ba lọt vì test cũ MỚM SẴN tham số đúng rồi mới kiểm hàm. Chúng chỉ lộ ra khi
có người gõ một câu tiếng Việt thật. Cái sai nằm ở bước dịch câu chữ thành tham
số truy vấn - không phải ở hàm nào bên dưới.

HAI TẦNG

  1. Tất định (luôn chạy): kiểm những gì CODE tự quyết được từ câu chữ, không
     cần LLM - khoanh vùng nội dung và dựng kỳ đối chiếu.
  2. `-m live`: chạy thật qua LLM + ERP, kiểm nguyên bộ tham số. Không nằm trong
     lần chạy mặc định vì cần GPU và CSDL, nhưng đây mới là tầng bắt được lỗi
     dịch câu chữ.
"""

from __future__ import annotations

import pytest

from app.agents.nodes.report import _compare_period, scope_from_request

CA_HAI = ["nhan_su", "thiet_bi"]
NHAN_SU = ["nhan_su"]
THIET_BI = ["thiet_bi"]

# Hôm nay trong các ca dưới đây là 19/09/2026, nên "kỳ gần nhất đã khép" là 2026-08.
KY_MAC_DINH = "2026-08"


# (câu hỏi, nội dung mong đợi, kỳ mong đợi hoặc None nếu không suy được từ câu chữ,
#  kỳ đối chiếu mong đợi hoặc None, mã đơn vị mong đợi hoặc None)
CORPUS: list[tuple[str, list[str], str | None, str | None, list[str] | None]] = [
    # --- khoanh vùng nội dung ---
    ("Tạo slide báo cáo thông tin nhân viên", NHAN_SU, KY_MAC_DINH, "2026-07", []),
    ("Báo cáo nhân sự tháng 8/2026", NHAN_SU, "2026-08", "2026-07", []),
    ("Thống kê cán bộ, biên chế của cơ quan", NHAN_SU, KY_MAC_DINH, "2026-07", []),
    ("slide tổng hợp nhân viên", NHAN_SU, KY_MAC_DINH, "2026-07", []),
    ("báo cáo biên chế và tuyển mới của đơn vị", NHAN_SU, KY_MAC_DINH, "2026-07", []),
    ("Làm slide báo cáo trang thiết bị tháng 8/2026", THIET_BI, "2026-08", "2026-07", []),
    ("tổng hợp trang thiết bị", THIET_BI, KY_MAC_DINH, "2026-07", []),
    ("slide tình hình thiết bị tháng 7/2026", THIET_BI, "2026-07", "2026-06", []),
    ("Tình hình tài sản, vật tư của cơ quan", THIET_BI, KY_MAC_DINH, "2026-07", []),
    ("Báo cáo tổng hợp tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),
    ("Báo cáo nhân sự và trang thiết bị tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),
    ("Làm cho tôi bộ slide báo cáo tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),

    # --- kỳ và kỳ đối chiếu ---
    ("slide nhân sự tháng 8/2026 so với tháng 8 năm 2025", NHAN_SU, "2026-08", "2025-08", []),
    ("slide nhân sự tháng 8/2026 so với cùng kỳ năm ngoái", NHAN_SU, "2026-08", "2025-08", []),
    ("báo cáo nhân sự tháng 8/2026 so với tháng 3", NHAN_SU, "2026-08", "2026-03", []),
    ("báo cáo trang thiết bị tháng 1/2026", THIET_BI, "2026-01", "2025-12", []),

    # --- phạm vi đơn vị ---
    ("slide nhân sự Phòng Kế toán tháng 8/2026", NHAN_SU, "2026-08", "2026-07", ["00003"]),
    ("báo cáo thiết bị của Phòng IT", THIET_BI, KY_MAC_DINH, "2026-07", ["00005"]),
    ("nhân sự Phòng Kinh doanh và Phòng Kỹ thuật tháng 8/2026",
     NHAN_SU, "2026-08", "2026-07", ["00001", "00002"]),

    # --- ca mơ hồ: nhắc cả hai mảng, code cố ý KHÔNG đè lên phán đoán của LLM ---
    ("thiết bị cấp cho nhân viên", None, KY_MAC_DINH, "2026-07", []),
]


def _ids() -> list[str]:
    return [case[0] for case in CORPUS]


# --------------------------------------------------------------------------- #
# Tầng 1: những gì code tự quyết được, không cần LLM
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cau, noi_dung, _ky, _ss, _dv", CORPUS, ids=_ids())
def test_khoanh_vung_noi_dung_theo_cau_chu(cau, noi_dung, _ky, _ss, _dv):
    """Câu nhắc đúng một mảng thì khoá vào mảng đó, bất kể LLM trích ra gì."""
    if noi_dung in (None, CA_HAI):
        # Câu nhắc cả hai mảng, hoặc không nhắc mảng nào: ý định thật sự mơ hồ,
        # code cố ý KHÔNG đè - trả lại nguyên lựa chọn của LLM, dù nó là gì.
        for lua_chon in (CA_HAI, list(reversed(CA_HAI)), NHAN_SU, THIET_BI):
            assert scope_from_request(cau, lua_chon) == lua_chon
        return

    # Câu nhắc đúng một mảng: đè được kể cả khi LLM đoán sai hoàn toàn.
    for lua_chon in (CA_HAI, list(reversed(CA_HAI)), NHAN_SU, THIET_BI):
        assert scope_from_request(cau, lua_chon) == noi_dung


@pytest.mark.parametrize("ky, nam, params, mong_doi", [
    ("2026-08", 2026, {}, "2026-07"),
    ("2026-01", 2026, {}, "2025-12"),
    ("2026-08", 2026, {"so_sanh_thang": 8, "so_sanh_nam": 2025}, "2025-08"),
    ("2026-08", 2026, {"so_sanh_thang": 3}, "2026-03"),
    # Trùng kỳ báo cáo -> mọi biến động bằng 0, vô nghĩa mà trông như có nghĩa.
    ("2026-08", 2026, {"so_sanh_thang": 8}, "2026-07"),
    # Nằm sau kỳ báo cáo.
    ("2026-08", 2026, {"so_sanh_thang": 11}, "2026-07"),
])
def test_ky_doi_chieu_dung_hoac_lui_ve_ky_lien_truoc(ky, nam, params, mong_doi):
    assert _compare_period(ky, nam, params, [])[0:7] == mong_doi


def test_moi_lui_ve_deu_duoc_noi_ra():
    """Lặng lẽ đổi kỳ đối chiếu là đổi ý nghĩa cả bản báo cáo."""
    for params in ({"so_sanh_thang": 8}, {"so_sanh_thang": 11}, {"so_sanh_thang": "abc"}):
        gia_dinh: list[str] = []
        _compare_period("2026-08", 2026, params, gia_dinh)
        assert gia_dinh, f"lùi về kỳ liền trước mà không ghi giả định: {params}"


# --------------------------------------------------------------------------- #
# Tầng 2: chạy thật qua LLM + ERP
# --------------------------------------------------------------------------- #
# Engine ERP và client LLM là singleton toàn tiến trình, gắn với event loop đã tạo
# ra chúng. Mỗi test một loop mới thì từ test thứ hai trở đi là "Event loop is
# closed" - lỗi của cách chạy test, không phải của sản phẩm. Dùng chung một loop
# cho cả module, và dọn sạch khi module xong để không ảnh hưởng file test khác.
_pytest_asyncio_module_loop = pytest.mark.asyncio(loop_scope="module")


@pytest.fixture(scope="module")
async def dong_ket_noi_khi_xong():
    """Chỉ tầng live mới mở kết nối, nên chỉ tầng live mới cần dọn."""
    yield
    from app.db.erp_session import dispose_erp_engine
    from app.services.llm import get_llm
    from app.services.presenton import close_presenton

    await get_llm().close()
    await dispose_erp_engine()
    await close_presenton()


@pytest.mark.live
@_pytest_asyncio_module_loop
@pytest.mark.parametrize("cau, noi_dung, ky, so_sanh, don_vi", CORPUS, ids=_ids())
async def test_trich_tham_so_that(cau, noi_dung, ky, so_sanh, don_vi,
                                  dong_ket_noi_khi_xong):
    """Câu tiếng Việt -> tham số truy vấn. Đây là bước hay sai nhất, và sai ở đây
    thì mọi van chắn phía sau đều vô dụng: số liệu đúng với một câu hỏi sai."""
    from app.agents.nodes.report import extract_params_node
    from app.core.context import Principal, use_principal

    with use_principal(Principal(tenant_id=64)):
        ket_qua = await extract_params_node({"request": cau, "history": [], "inputs": {}})

    params = ket_qua["params"]
    if noi_dung is not None:
        assert params["noi_dung"] == noi_dung
    if ky is not None:
        assert params["ky"] == ky
    if so_sanh is not None:
        assert params["compare_to"] == so_sanh
    if don_vi is not None:
        assert sorted(params["ma_don_vi"]) == sorted(don_vi)


# --------------------------------------------------------------------------- #
# Tầng 3: cấu trúc bộ slide dựng ra từ câu hỏi thật
#
# Tham số đúng chưa đủ. Ba lỗi từng lọt lưới đều nằm SAU bước tham số: `focus`
# model trả về cả câu, bảng 33 dòng bị cắt còn 8, nhãn "5 slide" trên file 7 slide.
# Những bất biến dưới đây kiểm đúng khoảng đó.
# --------------------------------------------------------------------------- #
CAU_DUNG_SLIDE = [
    ("Tạo slide báo cáo thông tin nhân viên", "nhan_su"),
    ("tổng hợp trang thiết bị", "thiet_bi"),
    ("slide nhân sự Phòng Kế toán tháng 8/2026", "nhan_su"),
    ("Báo cáo tổng hợp tháng 8/2026", None),
]

MANG_NGUOC = {"nhan_su": "thiet_bi", "thiet_bi": "nhan_su"}
KHOA_CUA_MANG = {"nhan_su": ("personnel", "personnel_breakdown"),
                 "thiet_bi": ("equipment", "equipment_breakdown")}


@pytest.mark.live
@_pytest_asyncio_module_loop
@pytest.mark.parametrize("cau, mang", CAU_DUNG_SLIDE, ids=[c for c, _ in CAU_DUNG_SLIDE])
async def test_bo_slide_dung_voi_cau_hoi(cau, mang, dong_ket_noi_khi_xong):
    """Câu tiếng Việt -> bộ slide .pptx thật, dựng bằng Presenton.

    Bộ slide do bên ngoài sinh chữ, nên ba bất biến dưới đây là thứ duy nhất giữ
    cho nó dùng được: đúng mảng số liệu được hỏi, không con số nào ngoài dữ liệu,
    và bảng chi tiết đủ dòng.
    """
    from pptx import Presentation

    from app.agents.graph import run_presentation_workflow
    from app.core.context import Principal, use_principal

    with use_principal(Principal(tenant_id=64)):
        ket_qua = await run_presentation_workflow(
            cau, inputs={"nguoi_trinh_bay": "Nguyễn Tiến Anh"})

    assert not ket_qua["error"], ket_qua["error"]
    # Đường lùi tự dựng vẫn ra file, nhưng ở tầng live thì nó là thất bại: nghĩa
    # là Presenton không dùng được và cả bộ slide mất phần diễn giải.
    assert ket_qua["engine"] == "presenton", ket_qua["assumptions"]

    # Hỏi một mảng thì bản tóm tắt không được mang mảng kia sang.
    brief = ket_qua["brief"]
    if mang == "nhan_su":
        assert "TRANG THIẾT BỊ" not in brief
    elif mang == "thiet_bi":
        assert "NHÂN SỰ" not in brief

    presentation = Presentation(ket_qua["output_path"])
    assert ket_qua["slide_count"] == len(presentation.slides)

    # Mọi con số trên slide phải truy được về dữ liệu gốc. Bản cũ trượt đúng chỗ
    # này: "8 đơn vị thiếu dữ liệu nhân sự" trong khi danh sách thật có 9.
    assert ket_qua["validation"]["issues"] == [], \
        [i["quote"] for i in ket_qua["validation"]["issues"]]

    chu = " ".join(sh.text_frame.text for s in presentation.slides
                   for sh in s.shapes if sh.has_text_frame)
    assert "xem chi tiết trong báo cáo" not in chu

    # Và quan trọng hơn cả: ĐỦ DÒNG.
    #
    # Không đếm ô bảng nữa: Presenton render bảng bằng các ô chữ đặt theo lưới
    # chứ không phải đối tượng bảng của PowerPoint. Bất biến thật nằm ở chỗ khác
    # và chặt hơn - MỌI dòng của bảng chi tiết phải có mặt trong bộ slide. Cắt
    # 33 dòng còn 8 là trượt ngay.
    for ten in await _ten_tung_dong(ket_qua["params"]):
        assert ten in chu, f"bảng chi tiết thiếu dòng {ten!r}"


async def _ten_tung_dong(params: dict) -> list[str]:
    """Hỏi thẳng tool số liệu: bảng chi tiết đáng lẽ có những dòng nào."""
    from app.db.erp_session import erp_session_scope
    from app.tools.data import get_equipment_statistics, get_personnel_statistics

    ham = {"nhan_su": get_personnel_statistics, "thiet_bi": get_equipment_statistics}
    nhom = {"nhan_su": "chuc_vu", "thiet_bi": "chung_loai"}

    ten: list[str] = []
    async with erp_session_scope() as session:
        for mang in params["noi_dung"]:
            ket_qua = await ham[mang](
                session, params["ky"], ma_don_vi=params["ma_don_vi"] or None,
                compare_to=params["compare_to"],
                group_by=nhom[mang] if params.get("nhom_theo") == nhom[mang]
                else "phong_ban")
            ten += [row["ten_nhom"] for row in ket_qua.breakdown]
    return ten


# --------------------------------------------------------------------------- #
# Chiều gộp: từ câu tiếng Việt tới nhãn cột trong file
#
# Đây là biến thể truy vấn duy nhất model được chọn, nên nó cũng là chỗ mới để
# sai: gộp nhầm chiều thì mọi con số vẫn đúng, chỉ có câu hỏi là khác.
# --------------------------------------------------------------------------- #
CORPUS_NHOM: list[tuple[str, str | None]] = [
    ("Báo cáo tổng hợp nhân sự theo chức vụ tháng 8/2026", "chuc_vu"),
    ("Thống kê nhân sự theo từng chức vụ", "chuc_vu"),
    ("Tổng hợp trang thiết bị theo chủng loại tháng 8/2026", "chung_loai"),
    ("báo cáo thiết bị theo loại thiết bị", "chung_loai"),
    # Không nêu chiều nào thì phải là None - gộp theo đơn vị như cũ.
    ("Báo cáo tổng hợp tháng 8/2026", None),
    ("Báo cáo nhân sự Phòng Kế toán tháng 8/2026", None),
]


@pytest.mark.live
@_pytest_asyncio_module_loop
@pytest.mark.parametrize("cau, nhom", CORPUS_NHOM, ids=[c for c, _ in CORPUS_NHOM])
async def test_trich_chieu_gop_that(cau, nhom, dong_ket_noi_khi_xong):
    from app.agents.nodes.report import extract_params_node
    from app.core.context import Principal, use_principal

    with use_principal(Principal(tenant_id=64)):
        ket_qua = await extract_params_node({"request": cau, "history": [], "inputs": {}})

    assert ket_qua["params"]["nhom_theo"] == nhom


@pytest.mark.live
@_pytest_asyncio_module_loop
async def test_bao_cao_gop_theo_chuc_vu_dung_nhan_va_dung_tong(dong_ket_noi_khi_xong):
    """Gộp là chia lại các dòng, không phải lọc: tổng cột phải bằng chỉ tiêu tổng.

    Kiểm trên FILE chứ không trên dict: nhãn cột sai thì chỉ nhìn file mới thấy -
    bảng in "Đơn vị" trên một cột đang chứa tên chức vụ.
    """
    from docx import Document

    from app.agents.graph import run_aggregate_workflow
    from app.core.context import Principal, use_principal

    with use_principal(Principal(tenant_id=64)):
        ket_qua = await run_aggregate_workflow(
            "Báo cáo tổng hợp nhân sự theo chức vụ tháng 8/2026", inputs={})

    assert not ket_qua["error"], ket_qua["error"]
    assert ket_qua["params"]["nhom_theo"] == "chuc_vu"
    assert any("chức vụ" in gia_dinh for gia_dinh in ket_qua["assumptions"])

    bang = [t for t in Document(ket_qua["output_path"]).tables
            if t.rows[0].cells[0].text.strip() == "Chức vụ"]
    assert bang, "không có bảng nào gộp theo chức vụ trong file"

    tong_cot = sum(int(r.cells[1].text) for r in bang[0].rows[1:] if r.cells[1].text.isdigit())
    assert tong_cot == ket_qua["data"]["personnel"]["metrics"]["total_personnel"]["value"]
