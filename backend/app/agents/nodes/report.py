"""Các node của workflow 4: tổng hợp báo cáo.

    yêu cầu ─> trích tham số ─> gọi data tool (SQL) ─> đối chiếu file báo cáo
            ─> vẽ biểu đồ (code) ─> viết nhận xét (LLM) ─> đối chiếu số ─> DOCX

Khác workflow 3 ở ba điểm: gộp nhiều đơn vị, so sánh giữa các kỳ, và có biểu đồ.
Mọi thứ còn lại - dựng mục, kiểm số, xuất file, ghi sổ - tái dùng nguyên.

Ranh giới không được vượt: **LLM không tính toán**. Tổng, hiệu, tỷ lệ phần trăm,
mức tăng giảm đều do `app.tools.data` tính sẵn; model chỉ diễn đạt. Biểu đồ cũng
do code vẽ, vì người đọc nhìn chiều cao cột chứ không đọc số.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import anyio

from app.agents.history import format_history
from app.agents import references
from app.agents import quyen
from app.agents.prompts import (
    AGG_NARRATIVE_SYSTEM,
    AGG_NARRATIVE_USER,
    AGG_PARAMS_SYSTEM,
    AGG_PARAMS_USER,
)
from app.core.config import get_settings
from app.documents.charts import compare_bar_chart, status_bar_chart
from app.documents.docx_builder import (
    DocumentPayload,
    RenderedSection,
    RenderedTable,
    build_docx,
)
from app.documents.extract_figures import reconcile_file
from app.documents.verify import check_numbers, collect_known_numbers
from app.db.erp_repository import ErpTaiNguyenRepository
from app.db.erp_session import erp_session_scope
from app.db.repository import TemplateRepository, VanBanRepository
from app.db.session import session_scope
from app.services import storage
from app.services.llm import LLMError, get_llm
from app.services import errors
from app.tools.data import ToolError, call_tool, previous_ky

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "total_personnel": "Tổng nhân sự", "new_hires": "Tuyển mới", "resignations": "Nghỉ việc",
    "total_equipment": "Tổng thiết bị", "good": "Tình trạng tốt",
    "needs_attention": "Cần xử lý",
    # "Số loại thiết bị" = số TÊN thiết bị khác nhau (33 trên dữ liệu thật), khác
    # hẳn "chủng loại" của ERP (`Asm_AssetCategories`, 8 nhóm). Nhãn cũ là "Số
    # chủng loại" nên báo cáo gộp theo chủng loại in ra "Số chủng loại: 33" ngay
    # phía trên một cái bảng chủng loại có 8 dòng.
    "equipment_types": "Số loại thiết bị",
}


async def next_so_ky_hieu(session, nam: int, ky_hieu: str) -> str:
    """Số thứ tự tiếp theo trong sổ văn bản của năm, cho một ký hiệu cụ thể.

    Văn bản hành chính đánh số thứ tự trong sổ ("88/BC-HCQT"), không đánh theo
    tháng - dạng "TH08/BC-HCQT" không qua được kiểm tra thể thức.

    Đếm theo KÝ HIỆU chứ không theo loại văn bản: mỗi đơn vị có sổ riêng, nên
    "BC-00003" và "BC-HCQT" là hai dãy số độc lập. Đếm chung thì báo cáo đầu tiên
    của một đơn vị đã mang số 45 vì các đơn vị khác đã phát hành 44 văn bản.
    """
    from sqlalchemy import extract, func, select

    from app.db.models import VanBan

    result = await session.execute(
        select(func.count()).select_from(VanBan).where(
            VanBan.ma_van_ban.like(f"%/{ky_hieu}"),
            extract("year", VanBan.ngay_van_ban) == nam,
        )
    )
    return f"{(result.scalar() or 0) + 1:02d}/{ky_hieu}"


def _ngay_tieng_viet(value: date) -> str:
    return f"ngày {value.day:02d} tháng {value.month} năm {value.year}"


# Cột mặc định cho số liệu cũ không khai `breakdown_columns` (báo cáo đọc từ tài
# liệu, dữ liệu đã lưu từ phiên trước).
_COT_MAC_DINH = {
    "personnel": [{"key": "ten_don_vi", "label": "Đơn vị"},
                  {"key": "nhan_su", "label": "Nhân sự"},
                  {"key": "nhan_su_ky_truoc", "label": "Kỳ trước"},
                  {"key": "tuyen_moi", "label": "Tuyển mới"},
                  {"key": "nghi_viec", "label": "Nghỉ việc"}],
    "equipment": [{"key": "ten_don_vi", "label": "Đơn vị"},
                  {"key": "ten_thiet_bi", "label": "Thiết bị"},
                  {"key": "so_luong", "label": "Số lượng"},
                  {"key": "tinh_trang", "label": "Tình trạng"}],
}


def bang_chi_tiet(source: dict[str, Any], mac_dinh: str) -> RenderedTable | None:
    """Bảng chi tiết dựng theo đúng cột mà TOOL khai, không viết cứng ở đây.

    Chiều gộp đổi thì nhãn cột đổi theo. Viết cứng danh sách cột ở nơi dựng bảng
    thì thêm một chiều gộp là phải nhớ sửa cả báo cáo lẫn slide, và quên một chỗ
    là bảng in nhãn "Đơn vị" trên cột đang chứa tên chức vụ.
    """
    rows = source.get("breakdown") or []
    if not rows:
        return None
    columns = source.get("breakdown_columns") or _COT_MAC_DINH[mac_dinh]
    return RenderedTable(
        columns=[c["label"] for c in columns],
        rows=[[str(row.get(c["key"], "")) for c in columns] for row in rows],
    )


# Số La Mã cho đầu mục văn bản hành chính.
#
# Trước đây đánh số bằng `"I" * index`, ra I, II, III rồi IIII, IIIII - đúng ba
# mục đầu và sai từ mục thứ tư. Báo cáo có đủ cả sáu mục thì mục cuối thành
# "IIIIII". Văn bản trình ký không đánh số kiểu đó.
_LA_MA = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def so_la_ma(n: int) -> str:
    """1 -> "I", 4 -> "IV", 6 -> "VI". Ngoài khoảng 1-39 thì trả lại số thường."""
    if not 1 <= n <= 39:
        return str(n)
    ket_qua = []
    for gia_tri, ky_hieu in _LA_MA:
        while n >= gia_tri:
            ket_qua.append(ky_hieu)
            n -= gia_tri
    return "".join(ket_qua)


def _compare_period(ky: str, nam: int, params: dict[str, Any],
                    assumptions: list[str]) -> str:
    """Kỳ đối chiếu. Mặc định là kỳ liền trước; người dùng nêu thì theo người dùng.

    Trước đây năm của kỳ đối chiếu bị gán cứng bằng năm của kỳ báo cáo, nên
    "tháng 8/2026 so với tháng 8 năm 2025" ra `compare_to = 2026-08` - so kỳ với
    CHÍNH NÓ. Slide in "biến động 0 (0%)" và người đọc hiểu là nhân sự không đổi
    suốt một năm. Một kết luận sai mà không con số nào trong đó là số bịa, nên
    không van chắn nào bắt được: phải chặn ngay từ chỗ dựng tham số.
    """
    thang_ss = params.get("so_sanh_thang")
    if not thang_ss:
        return previous_ky(ky)

    try:
        thang_ss = int(thang_ss)
        nam_ss = int(params.get("so_sanh_nam") or nam)
    except (TypeError, ValueError):
        assumptions.append("Không hiểu kỳ đối chiếu được nêu, lấy kỳ liền trước")
        return previous_ky(ky)

    if not 1 <= thang_ss <= 12:
        assumptions.append(f"Tháng đối chiếu {thang_ss} không hợp lệ, lấy kỳ liền trước")
        return previous_ky(ky)

    compare_to = f"{nam_ss:04d}-{thang_ss:02d}"
    if compare_to == ky:
        # Người dùng nêu một kỳ đối chiếu trùng kỳ báo cáo: gần như luôn là do
        # thiếu năm ("so với tháng 8" trong báo cáo tháng 8). So với chính mình
        # thì mọi biến động bằng 0 - vô nghĩa, và tệ hơn là trông như có nghĩa.
        assumptions.append(
            f"Kỳ đối chiếu nêu ra trùng kỳ báo cáo ({ky}), lấy kỳ liền trước thay thế")
        return previous_ky(ky)

    if compare_to > ky:
        assumptions.append(f"Kỳ đối chiếu {compare_to} nằm sau kỳ báo cáo {ky}, "
                           f"lấy kỳ liền trước thay thế")
        return previous_ky(ky)

    if nam_ss != nam:
        assumptions.append(f"Đối chiếu với kỳ {compare_to}, không phải kỳ liền trước")
    return compare_to


# --------------------------------------------------------------------------- #
# 1. Trích tham số
# Từ khoá khoanh vùng nội dung báo cáo.
#
# `noi_dung` do LLM trích ra, và nó đọc hụt: "báo cáo trang thiết bị" thì đúng,
# nhưng "báo cáo thông tin nhân viên" lại trả về CẢ HAI mảng - bộ slide xin về
# nhân sự mọc thêm biểu đồ thiết bị. Người dùng hỏi một mảng thì phải nhận đúng
# mảng đó, nên chỗ này không để LLM quyết một mình.
#
# Chỉ đè khi yêu cầu nhắc tới ĐÚNG MỘT mảng: nhắc cả hai, hoặc không nhắc mảng
# nào ("báo cáo tổng hợp"), thì giữ nguyên kết quả của LLM.
NOI_DUNG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nhan_su": ("nhân sự", "nhân viên", "cán bộ", "biên chế",
                "người lao động", "lao động", "tuyển mới", "nghỉ việc"),
    "thiet_bi": ("thiết bị", "trang thiết bị", "tài sản", "vật tư",
                 "phương tiện", "máy móc"),
}


# Chiều gộp cũng khoanh bằng từ khoá trước, y như `noi_dung`: câu "nhân sự theo
# chức vụ" nói rõ ràng tới mức không cần hỏi model, và model thì lúc trả lúc
# không. Chỉ đè khi câu chữ nêu ĐÚNG MỘT chiều.
NHOM_THEO_KEYWORDS: dict[str, tuple[str, ...]] = {
    "chuc_vu": ("theo chức vụ", "theo từng chức vụ", "theo chức danh", "theo vị trí"),
    "chung_loai": ("theo chủng loại", "theo từng chủng loại", "theo loại thiết bị",
                   "theo nhóm thiết bị", "theo danh mục"),
}


def dimension_from_request(request: str, llm_choice: Any) -> str | None:
    """Chiều gộp mà câu chữ thật sự yêu cầu; không rõ thì trả None (theo đơn vị)."""
    lower = (request or "").lower()
    nhac_toi = [key for key, words in NHOM_THEO_KEYWORDS.items()
                if any(word in lower for word in words)]
    if len(nhac_toi) == 1:
        return nhac_toi[0]
    return llm_choice if llm_choice in NHOM_THEO_KEYWORDS else None


def scope_from_request(request: str, llm_choice: list[str]) -> list[str]:
    """Mảng nội dung mà yêu cầu thật sự hỏi tới."""
    lower = (request or "").lower()
    nhac_toi = [key for key, words in NOI_DUNG_KEYWORDS.items()
                if any(word in lower for word in words)]
    if len(nhac_toi) == 1:
        return nhac_toi
    return llm_choice


# --------------------------------------------------------------------------- #
async def extract_params_node(state: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    async with erp_session_scope() as session:
        units = await ErpTaiNguyenRepository(session).list_don_vi()
    unit_lines = "\n".join(f"- {u['ma_don_vi']}: {u['ten_don_vi']}" for u in units)

    params: dict[str, Any] = {}
    try:
        params = await get_llm().chat_json(
            [
                {"role": "system", "content": AGG_PARAMS_SYSTEM.format(
                    today=today.strftime("%d/%m/%Y"), units=unit_lines)},
                {"role": "user", "content": AGG_PARAMS_USER.format(
                    history=format_history(state.get("history")), request=state["request"])},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=400,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không trích được tham số: %s", exc)

    thang, nam = params.get("thang"), params.get("nam")
    assumptions: list[str] = []
    if thang and not nam:
        nam = today.year
        assumptions.append(f"Không nêu năm, hiểu là năm {nam}")
    if not thang:
        # Không nêu kỳ thì lấy tháng trước - báo cáo tổng hợp luôn là của kỳ đã khép.
        previous_month = today.month - 1 or 12
        thang = previous_month
        nam = nam or (today.year if today.month > 1 else today.year - 1)
        assumptions.append(f"Không nêu kỳ, lấy kỳ gần nhất đã khép: tháng {thang}/{nam}")

    valid_codes = {u["ma_don_vi"] for u in units}
    requested = [c for c in (params.get("ma_don_vi") or []) if c in valid_codes]

    noi_dung = [c for c in (params.get("noi_dung") or []) if c in ("nhan_su", "thiet_bi")]
    noi_dung = scope_from_request(state.get("request", ""),
                                  noi_dung or ["nhan_su", "thiet_bi"])
    # Bỏ mảng người này không được xem. Cắt ở đây chứ không để truy vấn chạy rồi
    # mới lọc kết quả: số liệu không được phép đọc thì đừng đọc, chứ không phải
    # đọc xong rồi giấu đi.
    duoc_xem = quyen.loc_mang_duoc_xem(noi_dung)
    if duoc_xem != noi_dung:
        logger.info("Bỏ mảng %s khỏi báo cáo: thiếu quyền",
                    [m for m in noi_dung if m not in duoc_xem])
    noi_dung = duoc_xem
    ky = f"{nam:04d}-{thang:02d}"
    compare_to = _compare_period(ky, nam, params, assumptions)

    nhom_theo = dimension_from_request(state.get("request", ""), params.get("nhom_theo"))
    if nhom_theo:
        # Nói ra chiều gộp: cùng một con số tổng, chia theo chức vụ hay theo đơn
        # vị ra hai bảng khác hẳn nhau, và người đọc cần biết mình đang xem cái nào.
        assumptions.append(
            f"Bảng chi tiết gộp theo {'chức vụ' if nhom_theo == 'chuc_vu' else 'chủng loại'}")

    return {
        "params": {"ky": ky, "thang": thang, "nam": nam, "compare_to": compare_to,
                   "ma_don_vi": requested, "noi_dung": noi_dung,
                   "nhom_theo": nhom_theo},
        "assumptions": assumptions,
    }


# --------------------------------------------------------------------------- #
# 2. Lấy số liệu: từ CSDL, hoặc từ chính báo cáo các đơn vị đã gửi
# --------------------------------------------------------------------------- #
async def _files_from_register(session, ky: str, scope: list[str] | None) -> list[tuple[str, str, str]]:
    """(mã đơn vị, tên đơn vị, đường dẫn file) của các báo cáo đã vào sổ."""
    status = await call_tool(session, "get_reporting_status", ky=ky, ma_don_vi=scope)
    return [(item["ma_don_vi"], item.get("ten_don_vi", ""), item.get("file_path", ""))
            for item in status.get("reported", []) if item.get("file_path")]


async def _gather_from_documents(state: dict[str, Any], session) -> dict[str, Any]:
    """Đọc thẳng bảng kiểm kê trong các báo cáo đơn vị.

    Dùng khi chưa có CSDL kiểm kê, hoặc khi người dùng muốn con số đúng bằng thứ
    đơn vị đã ký gửi lên chứ không phải thứ đã nhập vào hệ thống.
    """
    import anyio

    from app.documents.collect import aggregate_reports, read_unit_report
    from app.services import storage

    params = state["params"]
    scope = params["ma_don_vi"] or None
    targets = await _files_from_register(session, params["ky"], scope)

    # File người dùng chỉ đích danh (đã tải lên) luôn được đọc, kể cả chưa vào sổ.
    for file_id in state.get("inputs", {}).get("files", "").split(","):
        if not file_id.strip():
            continue
        try:
            ref = storage.resolve(file_id.strip())
        except storage.StorageError as exc:
            logger.warning("Bỏ qua file %r: %s", file_id, exc)
            continue
        targets.append(("", "", str(ref.path)))

    if not targets:
        return {"error": "Không có báo cáo đơn vị nào để đọc. Hãy tải báo cáo lên rồi "
                         "truyền file_id qua inputs.files, hoặc vào sổ văn bản trước."}

    reports = await anyio.to_thread.run_sync(
        lambda: [read_unit_report(path, code, name) for code, name, path in targets]
    )
    equipment = aggregate_reports(reports, params["ky"])
    logger.info("Tổng hợp từ tài liệu: %d/%d báo cáo đọc được, tổng thiết bị %s",
                len(equipment["sources"]), len(targets),
                equipment["metrics"]["total_equipment"]["value"])
    return {
        "equipment": equipment,
        "unit_reports": [r.as_dict() for r in reports],
    }


async def gather_data_node(state: dict[str, Any]) -> dict[str, Any]:
    params = state["params"]
    scope = params["ma_don_vi"] or None
    data: dict[str, Any] = {}

    # Nguồn số liệu do người dùng chọn; mặc định vẫn là CSDL vì chỉ CSDL mới có
    # kỳ trước để so sánh tăng/giảm.
    nguon = str(state.get("inputs", {}).get("nguon_so_lieu", "csdl")).lower()
    if nguon in ("tai_lieu", "file", "document"):
        async with erp_session_scope() as session:
            try:
                data["reporting"] = await call_tool(session, "get_reporting_status",
                                                    ky=params["ky"], ma_don_vi=scope)
            except ToolError as exc:
                return {"error": f"Tham số truy vấn không hợp lệ: {exc}"}
            gathered = await _gather_from_documents(state, session)
        if "error" in gathered:
            return gathered
        data.update(gathered)
        data["nguon_so_lieu"] = "tai_lieu"
        return {"data": data}

    data["nguon_so_lieu"] = "csdl"
    try:
        return await _gather_from_database(state, params, scope, data)
    except ToolError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - CSDL hỏng là sự cố hạ tầng, không phải lỗi yêu cầu
        # Trả về `error` thay vì ném lên: workflow dừng gọn, agent tổng còn đổi
        # được sang nguồn khác thay vì cả kế hoạch đổ theo.
        return {"error": errors.as_error(exc, "lấy số liệu từ CSDL nghiệp vụ")}


async def _gather_from_database(state, params, scope, data) -> dict[str, Any]:
    async with erp_session_scope() as session:
        try:
            status = await call_tool(session, "get_reporting_status", ky=params["ky"],
                                     ma_don_vi=scope)
            data["reporting"] = status

            # Hai chiều gộp có tên khác nhau nên không cần hỏi "của mảng nào":
            # "chuc_vu" chỉ nhân sự hiểu, "chung_loai" chỉ thiết bị hiểu.
            nhom_theo = params.get("nhom_theo")

            if "nhan_su" in params["noi_dung"]:
                result = await call_tool(
                    session, "get_personnel_statistics",
                    ky=params["ky"], ma_don_vi=scope, compare_to=params["compare_to"],
                    group_by="chuc_vu" if nhom_theo == "chuc_vu" else "phong_ban")
                data["personnel"] = result.as_dict()
                data["personnel_consistent"] = result.is_consistent

            if "thiet_bi" in params["noi_dung"]:
                result = await call_tool(
                    session, "get_equipment_statistics",
                    ky=params["ky"], ma_don_vi=scope, compare_to=params["compare_to"],
                    group_by="chung_loai" if nhom_theo == "chung_loai" else "phong_ban")
                data["equipment"] = result.as_dict()
        except ToolError as exc:
            return {"error": f"Tham số truy vấn không hợp lệ: {exc}"}

    if not any(k in data for k in ("personnel", "equipment")):
        return {"error": "Không có nội dung nào để tổng hợp"}
    return {"data": data}


# --------------------------------------------------------------------------- #
# 3. Đối chiếu với file báo cáo đơn vị đã gửi
# --------------------------------------------------------------------------- #
async def reconcile_node(state: dict[str, Any]) -> dict[str, Any]:
    """CSDL là số chuẩn; đọc lại file để phát hiện đơn vị báo cáo lệch."""
    if state.get("error"):
        return {}

    data = state.get("data", {})
    # Số liệu đã lấy thẳng từ tài liệu thì không còn hai bên để đối chiếu.
    if data.get("nguon_so_lieu") == "tai_lieu":
        return {"reconciliation": [], "has_discrepancy": False}

    reporting = data.get("reporting", {})

    # Đối chiếu là phép so THEO ĐƠN VỊ: số trong báo cáo của đơn vị X với số kiểm
    # kê của đơn vị X. Bảng đã gộp theo chức vụ hay chủng loại thì không còn dòng
    # nào quy về được một đơn vị - bỏ qua phần đó thay vì so bừa.
    def _theo_don_vi(key: str) -> list[dict[str, Any]]:
        source = data.get(key) or {}
        if source.get("dimension", "phong_ban") != "phong_ban":
            return []
        return source.get("breakdown", [])

    personnel = {row["ma_don_vi"]: row for row in _theo_don_vi("personnel")}

    equipment_totals: dict[str, int] = {}
    for row in _theo_don_vi("equipment"):
        ma = row["ma_don_vi"]
        equipment_totals[ma] = equipment_totals.get(ma, 0) + row["so_luong"]

    results: list[dict[str, Any]] = []
    for item in reporting.get("reported", []):
        code = item["ma_don_vi"]
        # Chỉ đối chiếu được chỉ tiêu mà ERP có nguồn. File đơn vị thường còn ghi
        # có mặt/vắng, nhưng ERP không có dữ liệu chấm công nên không có gì để so;
        # đối chiếu một chiều sẽ báo lệch giả, tệ hơn là không đối chiếu.
        db_values = {
            **{k: v for k, v in personnel.get(code, {}).items() if k == "nhan_su"},
            **({"tong_so_thiet_bi": equipment_totals[code]} if code in equipment_totals else {}),
        }
        result = await anyio.to_thread.run_sync(
            lambda c=code, f=item.get("file_path"), d=db_values: reconcile_file(c, f, d)
        )
        results.append(result.as_dict())

    mismatched = [r for r in results if r["status"] == "mismatched"]
    return {"reconciliation": results, "has_discrepancy": bool(mismatched)}


# --------------------------------------------------------------------------- #
# 4. Vẽ biểu đồ - bằng code
# --------------------------------------------------------------------------- #
def _build_charts(data: dict[str, Any], params: dict[str, Any]) -> dict[str, bytes]:
    charts: dict[str, bytes] = {}
    period_label = f"tháng {params['thang']}/{params['nam']}"
    compare_label = f"tháng {int(params['compare_to'][5:])}/{params['compare_to'][:4]}"

    if (personnel := data.get("personnel")):
        metrics = personnel["metrics"]
        labels = [METRIC_LABELS[k] for k in metrics]
        charts["personnel"] = compare_bar_chart(
            f"Nhân sự {period_label} so với {compare_label}",
            labels,
            [m["value"] for m in metrics.values()],
            [m["prev"] or 0 for m in metrics.values()] if personnel.get("compare_to") else None,
            current_label=period_label.capitalize(), previous_label=compare_label.capitalize(),
        )

    if (equipment := data.get("equipment")):
        by_status: dict[str, int] = {}
        for row in equipment["breakdown"]:
            # Breakdown của CSDL có một dòng cho mỗi (thiết bị, tình trạng); của
            # tài liệu thì mỗi dòng là một chủng loại, tình trạng nằm ở các cột.
            if (status := row.get("tinh_trang")):
                by_status[status] = by_status.get(status, 0) + (row.get("so_luong") or 0)
                continue
            for field_name, label in (("hoat_dong_tot", "Hoạt động tốt"),
                                      ("can_xu_ly", "Cần xử lý")):
                if row.get(field_name) is not None:
                    by_status[label] = by_status.get(label, 0) + row[field_name]
        if by_status:
            charts["equipment"] = status_bar_chart(
                f"Tình trạng trang thiết bị {period_label}",
                list(by_status), list(by_status.values()),
            )
    return charts


async def charts_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}
    charts = await anyio.to_thread.run_sync(
        lambda: _build_charts(state["data"], state["params"])
    )
    return {"charts": charts}


# --------------------------------------------------------------------------- #
# 5. Dựng các mục
# --------------------------------------------------------------------------- #
def _metric_sentence(metrics: dict[str, Any]) -> str:
    """Câu liệt kê chỉ tiêu do CODE dựng."""
    parts = []
    for key, metric in metrics.items():
        label = METRIC_LABELS.get(key, key)
        text = f"{label}: {metric['value']}"
        if metric.get("share_pct") is not None:
            text += f" ({metric['share_pct']}%)"
        parts.append(text)
    return "; ".join(parts) + "."


def _metric_refs(payload: dict[str, Any]) -> list[references.Reference]:
    """Mỗi chỉ tiêu là một nguồn trích dẫn được.

    Mức này là mức người đọc quan tâm: "tổng nhân sự" chứ không phải "ô 113".
    """
    refs = [
        references.make("du_lieu", METRIC_LABELS.get(key, key),
                        json.dumps(value, ensure_ascii=False, default=str),
                        {"metric": key})
        for key, value in (payload.get("metrics") or {}).items()
    ]
    if (scope := payload.get("scope")):
        refs.append(references.make("du_lieu", "Phạm vi số liệu",
                                    json.dumps(scope, ensure_ascii=False, default=str),
                                    {"scope": True}))
    return references.number(refs)


async def _narrative(section_title: str, narrative: str, payload: dict[str, Any],
                     params: dict[str, Any]) -> tuple[list[str], list[str],
                                                      list[references.Reference]]:
    """Trả (đoạn sạch, đoạn còn marker, nguồn đã dùng).

    Bản sạch đi vào DOCX và đi vào bước đối chiếu số - marker "[1]" mà lọt vào
    `check_numbers` sẽ bị đọc thành con số 1 không truy được về dữ liệu gốc.
    Bản còn marker để UI dựng chỗ bấm.
    """
    compare = f" (so với kỳ {params['compare_to']})" if params.get("compare_to") else ""
    refs = _metric_refs(payload)
    try:
        result = await get_llm().chat_json(
            [
                {"role": "system", "content": AGG_NARRATIVE_SYSTEM},
                {"role": "user", "content": AGG_NARRATIVE_USER.format(
                    section_title=section_title, period=params["ky"], compare=compare,
                    narrative=narrative, data=references.render(refs))},
            ],
            temperature=0.2, max_tokens=900, thinking=False,
        )
        marked = [str(p).strip() for p in (result.get("paragraphs") or []) if str(p).strip()]
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không viết được mục %s: %s", section_title, exc)
        return [], [], []

    clean = [references.strip_markers(p) for p in marked]
    cited, used = references.renumber(marked, refs) if any(
        references.MARKER_RE.search(p) for p in marked) else (marked, [])
    return [p for p in clean if p], cited, used


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}

    data, params = state["data"], state["params"]
    charts = state.get("charts", {})
    period_label = f"tháng {params['thang']}/{params['nam']}"
    sections: list[dict[str, Any]] = []
    index = 1

    # --- I. Tình hình nộp báo cáo: thuần số liệu, không cần LLM ---
    # Bỏ mục này khi số liệu đọc thẳng từ tài liệu: sổ văn bản nói "0 đơn vị đã
    # gửi" ngay cạnh mục "đọc từ 2 báo cáo" thì người đọc không biết tin bên nào.
    # Mục NGUỒN SỐ LIỆU bên dưới đã nói đúng thứ thực sự được đọc.
    reporting = data.get("reporting", {})
    if reporting and data.get("nguon_so_lieu") != "tai_lieu":
        missing = reporting.get("missing", [])
        facts = (f"Tổng số {reporting['units_total']} đơn vị, "
                 f"{reporting['units_reported']} đơn vị đã gửi báo cáo.")
        if missing:
            facts += (" Các đơn vị chưa gửi: "
                      + ", ".join(m["ten_don_vi"] for m in missing) + ".")
        sections.append({"id": "nop_bao_cao", "title": f"{so_la_ma(index)}. TÌNH HÌNH GỬI BÁO CÁO",
                         "paragraphs": [facts], "table": None, "image": None,
                         "image_caption": "", "llm_written": []})
        index += 1

    # --- Nguồn số liệu, khi đọc thẳng từ báo cáo đơn vị ---
    # Người đọc phải biết con số đến từ đâu: cùng một kỳ, số trong tài liệu và số
    # trong CSDL có thể khác nhau, và bản báo cáo này chỉ phản ánh một bên.
    if data.get("nguon_so_lieu") == "tai_lieu":
        equipment_src = data.get("equipment", {})
        sources = equipment_src.get("sources", [])
        failed = equipment_src.get("failed", [])
        facts = (f"Số liệu trong báo cáo này được đọc trực tiếp từ {len(sources)} báo cáo "
                 f"đơn vị đã gửi, không lấy từ cơ sở dữ liệu kiểm kê.")
        if failed:
            facts += (" Không đọc được: "
                      + "; ".join(f"{f['file']} ({f['ly_do']})" for f in failed) + ".")
        paragraphs = [facts, *equipment_src.get("notes", [])]
        # Các mục khác lưu bảng dạng dict vì bước export dựng lại bằng
        # RenderedTable(**...); giữ đúng quy ước đó.
        table = {
            "columns": ["Đơn vị", "Tệp báo cáo", "Số ký hiệu", "Số dòng bảng", "Tổng thiết bị"],
            "rows": [[src.get("ten_don_vi") or src.get("ma_don_vi") or "—", src["file"],
                      src.get("so_ky_hieu") or "—", str(src.get("so_dong_bang", 0)),
                      str(src.get("figures", {}).get("tong", 0))] for src in sources],
        } if sources else None
        sections.append({"id": "nguon_so_lieu", "title": f"{so_la_ma(index)}. NGUỒN SỐ LIỆU",
                         "paragraphs": paragraphs, "table": table, "image": None,
                         "image_caption": "", "llm_written": []})
        index += 1

    # --- II. Nhân sự ---
    if (personnel := data.get("personnel")):
        title = f"{so_la_ma(index)}. TÌNH HÌNH NHÂN SỰ"
        facts = _metric_sentence(personnel["metrics"])
        payload = {"metrics": personnel["metrics"], "scope": personnel["scope"]}
        written, written_cited, refs = await _narrative(
            title,
            "Nhận xét về nhân sự: nêu mức tăng/giảm so với kỳ trước và biến động tuyển "
            "mới/nghỉ việc. Dùng đúng các giá trị delta, delta_pct, share_pct đã cho.",
            payload, params,
        )
        table = bang_chi_tiet(personnel, "personnel")
        sections.append({
            "id": "nhan_su", "title": title, "paragraphs": [facts, *written],
            "table": {"columns": table.columns, "rows": table.rows} if table else None,
            "image": charts.get("personnel"),
            "image_caption": f"Biểu đồ: Nhân sự {period_label} so với kỳ trước",
            # `llm_written` là bản sạch đi vào file; `_cited` giữ marker cho UI.
            "llm_written": written, "llm_written_cited": written_cited,
            "refs": refs, "source_data": payload,
        })
        index += 1

    # --- III. Trang thiết bị ---
    if (equipment := data.get("equipment")):
        title = f"{so_la_ma(index)}. TÌNH HÌNH TRANG THIẾT BỊ"
        facts = _metric_sentence(equipment["metrics"])
        payload = {"metrics": equipment["metrics"], "scope": equipment["scope"]}
        written, written_cited, refs = await _narrative(
            title,
            "Nhận xét về trang thiết bị: nêu tổng số lượng, số chủng loại và mức "
            "tăng/giảm so với kỳ trước. Chỉ nhận xét về tình trạng tốt/cần xử lý "
            "nếu SỐ LIỆU có hai chỉ tiêu đó.",
            payload, params,
        )
        # Bảng chi tiết là chỗ DUY NHẤT người đọc đối chiếu được con số tổng về
        # từng đơn vị. Mục nhân sự có bảng, mục thiết bị thì trước đây để cứng
        # `"table": None` - báo cáo ghi "tổng 194 thiết bị" mà không một dòng nào
        # nói 194 đó nằm ở đâu.
        table = bang_chi_tiet(equipment, "equipment")

        sections.append({
            "id": "thiet_bi", "title": title, "paragraphs": [facts, *written],
            "table": {"columns": table.columns, "rows": table.rows} if table else None,
            "image": charts.get("equipment"),
            "image_caption": f"Biểu đồ: Tình trạng trang thiết bị {period_label}",
            "llm_written": written, "llm_written_cited": written_cited,
            "refs": refs, "source_data": payload,
        })
        index += 1

    # --- IV. Đối chiếu: chỉ xuất hiện khi thực sự có chênh lệch ---
    if state.get("has_discrepancy"):
        rows = [
            [item["ma_don_vi"], d["label"], str(d["file_value"]), str(d["db_value"])]
            for item in state.get("reconciliation", [])
            for d in item["discrepancies"]
        ]
        sections.append({
            "id": "doi_chieu", "title": f"{so_la_ma(index)}. ĐỐI CHIẾU SỐ LIỆU",
            "paragraphs": ["Phát hiện chênh lệch giữa số liệu trong báo cáo của đơn vị "
                           "và số liệu kiểm kê trong hệ thống:"],
            "table": {"columns": ["Đơn vị", "Chỉ tiêu", "Báo cáo ghi", "Kiểm kê"],
                      "rows": rows},
            "image": None, "image_caption": "", "llm_written": [],
        })
        index += 1

    # --- V. Cảnh báo số liệu không nhất quán ---
    # Gộp cả nhân sự lẫn thiết bị: mục này tên là "số liệu cần kiểm tra lại", bỏ
    # sót nguồn nào thì chính chỗ đáng ngờ nhất lại là chỗ im lặng.
    inconsistent = [
        *data.get("personnel", {}).get("consistency", []),
        *data.get("equipment", {}).get("consistency", []),
    ]
    if inconsistent:
        sections.append({
            "id": "canh_bao", "title": f"{so_la_ma(index)}. SỐ LIỆU CẦN KIỂM TRA LẠI",
            "paragraphs": [item["message"] for item in inconsistent],
            "table": None, "image": None, "image_caption": "", "llm_written": [],
        })

    return {"sections": sections}


# --------------------------------------------------------------------------- #
# 6. Đối chiếu số trong văn bản
def _period_numbers(params: dict[str, Any]) -> set[str]:
    """Số của kỳ báo cáo và kỳ đối chiếu, dạng van chắn số chấp nhận được."""
    numbers = {str(v) for v in (params.get("thang"), params.get("nam")) if v}
    if (compare_to := params.get("compare_to")):
        nam, _, thang = str(compare_to).partition("-")
        numbers.update({nam, thang, str(int(thang)) if thang.isdigit() else thang})
    return numbers


# --------------------------------------------------------------------------- #
async def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {"validation": {"status": "skipped", "issues": []}}

    params = state.get("params", {})
    # Kỳ báo cáo VÀ kỳ đối chiếu đều là số hợp lệ trong văn bản. Thiếu kỳ đối
    # chiếu thì câu "so với tháng 8/2025" bị kết luận là bịa số, và cả mục bị
    # loại - trong khi đó chính là câu người đọc cần nhất.
    period_numbers = _period_numbers(params)

    issues: list[dict[str, Any]] = []
    checked_total = 0
    for section in state.get("sections", []):
        # Phạm vi kiểm tra đúng bằng số liệu đã đưa cho LLM ở mục này. Lấy cả `data`
        # làm tập hợp lệ thì một con số bịa như "tăng 5 người" vẫn lọt chỉ vì số 5
        # tình cờ xuất hiện ở chỗ khác trong dữ liệu.
        known = collect_known_numbers(section.get("source_data", {})) | period_numbers
        checked_total = max(checked_total, len(known))
        for paragraph in section.get("llm_written", []):
            check = check_numbers(paragraph, known, section["id"])
            if not check.ok:
                issues.append({"type": "unverified_number", "section": section["id"],
                               "numbers": check.unverified, "severity": "error",
                               "quote": paragraph[:160]})

    if not state.get("data", {}).get("personnel_consistent", True):
        issues.append({"type": "inconsistent_source", "section": "nhan_su",
                       "numbers": [], "severity": "warning",
                       "quote": "Số liệu nguồn không thoả ràng buộc nhân sự"})

    blocking = [i for i in issues if i["severity"] == "error"]
    return {"validation": {
        "status": "failed" if blocking else ("warning" if issues else "passed"),
        "issues": issues, "checked_numbers": checked_total,
    }}


# --------------------------------------------------------------------------- #
# 7. Xuất DOCX + ghi sổ
# --------------------------------------------------------------------------- #
async def export_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("validation", {}).get("status") == "failed":
        return {"output_path": ""}

    cfg = get_settings()
    params = state["params"]
    inputs = state.get("inputs", {})
    period_label = f"tháng {params['thang']}/{params['nam']}"

    async with session_scope() as session:
        template = await TemplateRepository(session).get("BC_TONGHOP")
        template_file = template.file_path if template else ""
        so_ky_hieu = inputs.get("so_ky_hieu") or await next_so_ky_hieu(
            session, params["nam"], "BC-HCQT"
        )

    payload = DocumentPayload(
        meta={
            "noi_gui": inputs.get("noi_gui", "PHÒNG HÀNH CHÍNH QUẢN TRỊ"),
            "noi_nhan": inputs.get("noi_nhan", "Ban Giám đốc"),
            "so_ky_hieu": so_ky_hieu,
            "dia_danh": inputs.get("dia_danh", "Hà Nội"),
            "ngay_bao_cao": _ngay_tieng_viet(date.today()),
            "trich_yeu": inputs.get(
                "trich_yeu", f"V/v tổng hợp tình hình nhân sự và trang thiết bị {period_label}"),
            "chuc_vu_ky": inputs.get("chuc_vu_ky", "TRƯỞNG PHÒNG"),
            "nguoi_ky": inputs.get("nguoi_ky", ""),
            "can_cu": inputs.get("can_cu", ""),
        },
        sections=[
            RenderedSection(
                id=s["id"], title=s["title"], paragraphs=s["paragraphs"],
                table=RenderedTable(**s["table"]) if s["table"] else None,
                image=s.get("image"), image_caption=s.get("image_caption", ""),
            )
            for s in state.get("sections", [])
        ],
    )

    stem = storage.versioned_stem(re.sub(r"[^A-Za-z0-9_.-]", "_", f"BC_TONGHOP_{params['ky']}"))
    output_path = Path(cfg.output_dir) / f"{stem}.docx"
    path = await anyio.to_thread.run_sync(
        lambda: build_docx(payload, output_path, template_file or None)
    )
    return {"output_path": str(path), "so_ky_hieu": so_ky_hieu}


async def register_node(state: dict[str, Any]) -> dict[str, Any]:
    output_path = state.get("output_path")
    if not output_path:
        return {}

    from app.db.models import VanBan

    params = state["params"]
    ma_van_ban = state.get("so_ky_hieu") or f"{params['thang']:02d}/BC-HCQT"
    try:
        async with session_scope() as session:
            await VanBanRepository(session).upsert(VanBan(
                ma_van_ban=ma_van_ban,
                ten_van_ban=f"Báo cáo tổng hợp nhân sự và trang thiết bị "
                            f"tháng {params['thang']}/{params['nam']}",
                loai_van_ban="bao_cao_tong_hop",
                # Báo cáo tổng hợp là của cơ quan, không của đơn vị nào - để
                # trống `ma_don_vi` thì nó không bị đếm nhầm là báo cáo đơn vị.
                ky=params["ky"],
                noi_gui="Phòng Hành chính nhân sự",
                noi_nhan="Ban Giám đốc",
                mo_ta=f"Tổng hợp kỳ {params['ky']}",
                ngay_van_ban=date.today(),
                file_path=output_path, dang_file="docx",
            ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không ghi được sổ văn bản: %s", exc)
        return {}
    return {"registered_as": ma_van_ban}
