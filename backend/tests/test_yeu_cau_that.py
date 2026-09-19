"""Câu hỏi thật của người dùng, chạy qua nguyên đường trích tham số.

VÌ SAO CÓ FILE NÀY

Ba lỗi nặng nhất của phần tạo slide đều lọt qua hơn 300 test:

  - `focus` model trả về cả câu thay vì một trong bốn giá trị -> ô chỉ tiêu hiện
    quân số dưới tiêu đề nói về trang bị;
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

CA_HAI = ["quan_so", "trang_bi"]
QUAN_SO = ["quan_so"]
TRANG_BI = ["trang_bi"]

# Hôm nay trong các ca dưới đây là 19/09/2026, nên "kỳ gần nhất đã khép" là 2026-08.
KY_MAC_DINH = "2026-08"


# (câu hỏi, nội dung mong đợi, kỳ mong đợi hoặc None nếu không suy được từ câu chữ,
#  kỳ đối chiếu mong đợi hoặc None, mã đơn vị mong đợi hoặc None)
CORPUS: list[tuple[str, list[str], str | None, str | None, list[str] | None]] = [
    # --- khoanh vùng nội dung ---
    ("Tạo slide báo cáo thông tin nhân viên", QUAN_SO, KY_MAC_DINH, "2026-07", []),
    ("Báo cáo quân số tháng 8/2026", QUAN_SO, "2026-08", "2026-07", []),
    ("Thống kê cán bộ, biên chế của cơ quan", QUAN_SO, KY_MAC_DINH, "2026-07", []),
    ("slide tổng hợp nhân viên", QUAN_SO, KY_MAC_DINH, "2026-07", []),
    ("báo cáo biên chế và tuyển mới của đơn vị", QUAN_SO, KY_MAC_DINH, "2026-07", []),
    ("Làm slide báo cáo trang thiết bị tháng 8/2026", TRANG_BI, "2026-08", "2026-07", []),
    ("tổng hợp trang thiết bị", TRANG_BI, KY_MAC_DINH, "2026-07", []),
    ("slide tình hình khí tài tháng 7/2026", TRANG_BI, "2026-07", "2026-06", []),
    ("Tình hình tài sản, vật tư của cơ quan", TRANG_BI, KY_MAC_DINH, "2026-07", []),
    ("Báo cáo tổng hợp tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),
    ("Báo cáo quân số và trang thiết bị tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),
    ("Làm cho tôi bộ slide báo cáo tháng 8/2026", CA_HAI, "2026-08", "2026-07", []),

    # --- kỳ và kỳ đối chiếu ---
    ("slide quân số tháng 8/2026 so với tháng 8 năm 2025", QUAN_SO, "2026-08", "2025-08", []),
    ("slide quân số tháng 8/2026 so với cùng kỳ năm ngoái", QUAN_SO, "2026-08", "2025-08", []),
    ("báo cáo quân số tháng 8/2026 so với tháng 3", QUAN_SO, "2026-08", "2026-03", []),
    ("báo cáo trang thiết bị tháng 1/2026", TRANG_BI, "2026-01", "2025-12", []),

    # --- phạm vi đơn vị ---
    ("slide quân số Phòng Kế toán tháng 8/2026", QUAN_SO, "2026-08", "2026-07", ["00003"]),
    ("báo cáo trang bị của Phòng IT", TRANG_BI, KY_MAC_DINH, "2026-07", ["00005"]),
    ("quân số Phòng Kinh doanh và Phòng Kỹ thuật tháng 8/2026",
     QUAN_SO, "2026-08", "2026-07", ["00001", "00002"]),

    # --- ca mơ hồ: nhắc cả hai mảng, code cố ý KHÔNG đè lên phán đoán của LLM ---
    ("trang bị cấp cho nhân viên", None, KY_MAC_DINH, "2026-07", []),
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
        for lua_chon in (CA_HAI, list(reversed(CA_HAI)), QUAN_SO, TRANG_BI):
            assert scope_from_request(cau, lua_chon) == lua_chon
        return

    # Câu nhắc đúng một mảng: đè được kể cả khi LLM đoán sai hoàn toàn.
    for lua_chon in (CA_HAI, list(reversed(CA_HAI)), QUAN_SO, TRANG_BI):
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
    ("Tạo slide báo cáo thông tin nhân viên", "quan_so"),
    ("tổng hợp trang thiết bị", "trang_bi"),
    ("slide quân số Phòng Kế toán tháng 8/2026", "quan_so"),
    ("Báo cáo tổng hợp tháng 8/2026", None),
]

MANG_NGUOC = {"quan_so": "trang_bi", "trang_bi": "quan_so"}
KHOA_CUA_MANG = {"quan_so": ("personnel", "personnel_breakdown"),
                 "trang_bi": ("equipment", "equipment_breakdown")}


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
    if mang == "quan_so":
        assert "TRANG THIẾT BỊ" not in brief
    elif mang == "trang_bi":
        assert "QUÂN SỐ" not in brief

    presentation = Presentation(ket_qua["output_path"])
    assert ket_qua["slide_count"] == len(presentation.slides)

    # Mọi con số trên slide phải truy được về dữ liệu gốc. Bản cũ trượt đúng chỗ
    # này: "8 đơn vị thiếu dữ liệu nhân sự" trong khi danh sách thật có 9.
    assert ket_qua["validation"]["issues"] == [], \
        [i["quote"] for i in ket_qua["validation"]["issues"]]

    bang = [sh.table for s in presentation.slides for sh in s.shapes if sh.has_table]
    assert bang, "bộ slide không có bảng chi tiết nào"
    assert all(len(t.rows) > 1 for t in bang), "có bảng chỉ còn dòng tiêu đề cột"

    chu = " ".join(sh.text_frame.text for s in presentation.slides
                   for sh in s.shapes if sh.has_text_frame)
    assert "xem chi tiết trong báo cáo" not in chu

    # Và quan trọng hơn cả: ĐỦ DÒNG.
    #
    # Chỉ kiểm "bảng không rỗng" thì không bắt được lối cắt cụt - cắt 33 dòng còn
    # 8 vẫn qua. Phải hỏi lại chính tool số liệu xem đáng lẽ có bao nhiêu dòng,
    # rồi đếm dòng thật trong file.
    mong_doi = await _so_dong_dang_le_co(ket_qua["params"])
    dem = _dem_dong_theo_cot(presentation)
    for cot_dau, so_dong in mong_doi.items():
        assert dem.get(cot_dau) == so_dong, (
            f"bảng '{cot_dau}': file có {dem.get(cot_dau)} dòng, "
            f"số liệu có {so_dong} dòng")


def _dem_dong_theo_cot(presentation) -> dict[str, int]:
    """Tổng số dòng dữ liệu của mỗi bảng, gộp các trang của cùng một bảng.

    Bảng dài được tách ra nhiều slide, nên đếm theo tiêu đề cột đầu tiên chứ không
    theo từng slide - nếu không thì bảng 3 trang bị đếm thành ba bảng riêng.
    """
    dem: dict[str, int] = {}
    for slide in presentation.slides:
        for shape in slide.shapes:
            if not shape.has_table:
                continue
            khoa = " | ".join(cell.text for cell in shape.table.rows[0].cells)
            dem[khoa] = dem.get(khoa, 0) + len(shape.table.rows) - 1
    return dem


async def _so_dong_dang_le_co(params: dict) -> dict[str, int]:
    """Hỏi thẳng tool số liệu: bảng này đáng lẽ có bao nhiêu dòng."""
    from app.db.erp_session import erp_session_scope
    from app.tools.data import get_equipment_statistics, get_personnel_statistics

    cot = {"quan_so": "Đơn vị | Quân số | Kỳ trước | Tuyển mới | Nghỉ việc",
           "trang_bi": "Đơn vị | Trang bị | Số lượng | Tình trạng"}
    ham = {"quan_so": get_personnel_statistics, "trang_bi": get_equipment_statistics}

    mong_doi: dict[str, int] = {}
    async with erp_session_scope() as session:
        for mang in params["noi_dung"]:
            ket_qua = await ham[mang](session, params["ky"],
                                      ma_don_vi=params["ma_don_vi"] or None,
                                      compare_to=params["compare_to"])
            if ket_qua.breakdown:
                mong_doi[cot[mang]] = len(ket_qua.breakdown)
    return mong_doi
