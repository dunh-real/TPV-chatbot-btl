"""Dựng PowerPoint từ dữ liệu truy vấn động hoặc tài liệu; kiểm tra số trong file đã xuất."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import anyio

from app.agents.nodes.report import METRIC_LABELS, _period_numbers
from app.core.config import get_settings
from app.documents.pptx_builder import (
    DeckSpec,
    MetricBox,
    SlideSpec,
    SlideTable,
    build_pptx,
    count_slides,
)
from app.documents.slide_deck import so as so_vn
from app.documents.verify import (
    check_numbers,
    collect_known_numbers,
    tim_chu_ngoai_he,
)
from app.documents.query_result import query_slides, query_deck
from app.services import storage
from app.services.presenton import PresentonError, get_presenton

logger = logging.getLogger(__name__)

# Giới hạn THẬT của template `general` bên Presenton, không phải con số chọn cho
# đẹp: layout bảng nhận tối đa 6 dòng, layout chỉ tiêu nhận 2-3 ô. Vượt ngưỡng
# thì schema từ chối, Presenton dựng lại ba lần rồi trả về bộ slide rỗng.
MAX_TABLE_ROWS_SLIDE = 6
MAX_METRICS_PER_SLIDE = 3

# Bảng chi tiết dài tới đâu thì vẫn liệt kê đủ trong bản tóm tắt. Cắt bảng là lỗi
# đã từng xảy ra (33 dòng còn 8) và nó âm thầm: người đọc cộng các dòng ra một số
# khác với số tổng in ở slide trước. Vượt ngưỡng này thì nói thẳng là đã cắt.
MAX_BRIEF_ROWS = 120


def _period_label(params: dict[str, Any]) -> str:
    return f"tháng {params['thang']}/{params['nam']}"


def _compare_label(params: dict[str, Any]) -> str:
    compare_to = str(params.get("compare_to") or "")
    if not compare_to or "-" not in compare_to:
        return ""
    nam, _, thang = compare_to.partition("-")
    return f"tháng {int(thang)}/{nam}"


def _so(value: Any) -> str:
    """Số đọc được trong văn bản tiếng Việt: 28 chứ không phải 28.0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _dong_chi_tieu(key: str, metric: dict[str, Any]) -> str:
    """Một chỉ tiêu kèm MỌI con số sẽ được nhắc tới - model không phải tính gì."""
    label = METRIC_LABELS.get(key, key)
    parts = [f"- {label}: {_so(metric['value'])}"]
    if metric.get("prev") is not None:
        parts.append(f"kỳ trước {_so(metric['prev'])}")
    if metric.get("delta") is not None:
        dau = "+" if metric["delta"] > 0 else ""
        bien_dong = f"biến động {dau}{_so(metric['delta'])}"
        if metric.get("delta_pct") is not None:
            bien_dong += f" ({dau}{_so(metric['delta_pct'])}%)"
        parts.append(bien_dong)
    if metric.get("share_pct") is not None:
        parts.append(f"chiếm {_so(metric['share_pct'])}%")
    return "; ".join(parts) + "."


def _bang(tieu_de: str, cot: list[str], dong: list[list[str]]) -> list[str]:
    """Số liệu từng đơn vị, dạng DANH SÁCH chứ không phải bảng.

    Cố ý không gọi là "bảng" và không kẻ khung: mọi template của Presenton đều
    giới hạn bảng ở 3-6 dòng, và khi model cố dựng một bảng 9 dòng thì schema từ
    chối, dựng lại ba lần rồi trả về bộ slide RỖNG - hỏng cả bộ vì một cái bảng.
    Bảng chi tiết thật do code ghép vào cuối file (xem `_bang_chi_tiet`); phần ở
    đây chỉ để model có số mà viết nhận xét.
    """
    if not dong:
        return []
    lines = [f"{tieu_de} - liệt kê để tham khảo, KHÔNG dựng thành bảng "
             f"({' / '.join(cot)}):"]
    for row in dong[:MAX_BRIEF_ROWS]:
        lines.append("- " + " / ".join(row))
    if len(dong) > MAX_BRIEF_ROWS:
        lines.append(f"- (còn {len(dong) - MAX_BRIEF_ROWS} dòng nữa không liệt kê ở đây)")
    return lines


def _bang_tu_tool(source: dict[str, Any], mac_dinh: str) -> list[str]:
    """Danh sách số liệu theo đúng cột tool khai, kèm nhãn chiều gộp."""
    from app.agents.nodes.report import bang_chi_tiet

    table = bang_chi_tiet(source, mac_dinh)
    if table is None:
        return []
    nhan = (source.get("scope") or {}).get("nhom_theo") or "đơn vị"
    return _bang(f"Chi tiết theo {str(nhan).lower()}", table.columns, table.rows)


# --------------------------------------------------------------------------- #
# 1. Bản tóm tắt số liệu - do CODE dựng
# --------------------------------------------------------------------------- #
def _slide_bia(data: dict[str, Any], params: dict[str, Any],
               inputs: dict[str, Any]) -> str:
    period = _period_label(params)
    dong_phu = []
    # Slide bìa có chỗ cho người trình bày; không nêu thì Presenton in "Chưa
    # cung cấp". Chỉ điền khi người dùng thật sự đưa vào - tự nghĩ ra một cái
    # tên ngay trang đầu thì tệ hơn một chỗ trống.
    for key in ("don_vi_trinh_bay", "nguoi_trinh_bay"):
        if (value := str(inputs.get(key) or "").strip()):
            dong_phu.append(value)
    if (compare := _compare_label(params)):
        dong_phu.append(f"Kỳ đối chiếu: {compare}")
    if (reporting := data.get("reporting")):
        dong_phu.append(f"Phạm vi: {reporting['units_total']} đơn vị")
    return f"# Báo cáo số liệu {period}\n\n" + " — ".join(dong_phu)


def _slide_chi_tieu(source: dict[str, Any], ten_mang: str, period: str) -> list[str]:
    """Slide chỉ tiêu. Tách mỗi slide tối đa 3 ô - layout của Presenton chặn ở đó."""
    muc = list(source.get("metrics", {}).items())
    slides: list[str] = []
    for i in range(0, len(muc), MAX_METRICS_PER_SLIDE):
        phan = muc[i:i + MAX_METRICS_PER_SLIDE]
        # Layout chỉ tiêu cần TỐI THIỂU hai ô; một ô lẻ thì gộp ngược lên slide
        # trước thay vì dựng một slide mà layout từ chối.
        if len(phan) == 1 and slides:
            slides[-1] += "\n" + _dong_chi_tieu(*phan[0])
            continue
        slides.append(f"## Chỉ tiêu {ten_mang} {period}\n\n"
                      + "\n".join(_dong_chi_tieu(k, m) for k, m in phan))
    return slides


def _slide_bang(source: dict[str, Any], mac_dinh: str, ten_mang: str,
                period: str) -> tuple[list[str], set[str]]:
    """Bảng chi tiết, cắt thành nhiều slide vừa sức layout của Presenton.

    Layout bảng của template `general` chặn ở 6 dòng. Đưa cả bảng 33 dòng thì
    schema từ chối, Presenton dựng lại ba lần rồi trả về bộ slide RỖNG - nên
    việc cắt trang phải làm ở đây, và cắt thì phải cắt ĐỦ: mỗi dòng đều lên
    slide, không dòng nào rơi.
    """
    from app.agents.nodes.report import bang_chi_tiet

    table = bang_chi_tiet(source, mac_dinh)
    if table is None:
        return [], set()

    nhan = (source.get("scope") or {}).get("nhom_theo") or "đơn vị"
    trang = [table.rows[i:i + MAX_TABLE_ROWS_SLIDE]
             for i in range(0, len(table.rows), MAX_TABLE_ROWS_SLIDE)]
    dau_bang = ("| " + " | ".join(table.columns) + " |\n"
                + "|" + "---|" * len(table.columns))

    slides: list[str] = []
    for index, dong in enumerate(trang, start=1):
        so_trang = f" ({index}/{len(trang)})" if len(trang) > 1 else ""
        than = "\n".join("| " + " | ".join(o) + " |" for o in dong)
        # Tiêu đề ngắn gọn có chủ đích. Bản cũ dài hơn ("Chi tiết trang thiết bị
        # theo đơn vị (1/6)", 42 ký tự) và Presenton tự rút gọn mỗi trang một
        # kiểu: "(1/6)" giữ nguyên, "(2/6)" mất chữ "Chi tiết", "4/6" mất cả
        # ngoặc. Sáu slide liền nhau bốn cách đặt tên - chiếu lên là thấy ngay
        # máy viết. Ngắn sẵn thì nó không có cớ sửa.
        slides.append(f"## {ten_mang.capitalize()} theo {str(nhan).lower()}{so_trang}\n\n"
                      f"{dau_bang}\n{than}")

    # Số trang do CODE viết ra, không phải model bịa - bước đối chiếu số phải
    # biết điều đó, nếu không "(2/6)" thành hai con số không truy được.
    so_cua_code = {str(index) for index in range(1, len(trang) + 1)}
    return slides, so_cua_code


def _slide_don_vi_chua_gui(reporting: dict[str, Any],
                           period: str) -> tuple[list[str], set[str]]:
    """Đơn vị chưa gửi báo cáo, dạng BẢNG chứ không phải câu liệt kê.

    Bản cũ nhét cả danh sách vào một câu ("Chưa gửi: Phòng A, Tổ B, ..."). Đo
    trên bốn bộ slide thật thì slide này là chỗ DUY NHẤT model làm hỏng chữ, và
    lần nào cũng hỏng:

        "...các phòng ban và tổ chuyên門"          (môn  -> 門)
        "Tổ Phân tích nghiệp vụ, Tổ Phát展展展"     (triển -> 展, rồi cụt danh sách)

    Toàn là từ Hán-Việt bị model thay bằng chữ Hán gốc, và ca thứ hai còn nuốt
    mất phần đuôi danh sách. Trong khi đó các slide BẢNG (nhân sự/thiết bị theo
    đơn vị) không sai một tên nào qua cả bốn lần dựng - vì bảng đi qua layout
    bảng, model chỉ xếp ô chứ không viết lại câu.

    Nên chuyển danh sách sang bảng: bỏ hẳn nguyên nhân thay vì dò chữ Hán sau
    khi file đã hỏng. Chốt CJK ở `verify_node` vẫn giữ, nó là lưới an toàn cho
    những chỗ khác.
    """
    missing = reporting.get("missing") or []
    if not missing:
        return [], set()

    rows = [[m["ma_don_vi"], m["ten_don_vi"]] for m in missing]
    trang = [rows[i:i + MAX_TABLE_ROWS_SLIDE]
             for i in range(0, len(rows), MAX_TABLE_ROWS_SLIDE)]
    dau_bang = "| Mã đơn vị | Đơn vị |\n|---|---|"

    slides: list[str] = []
    for index, dong in enumerate(trang, start=1):
        so_trang = f" ({index}/{len(trang)})" if len(trang) > 1 else ""
        than = "\n".join("| " + " | ".join(o) + " |" for o in dong)
        slides.append(f"## Đơn vị chưa gửi báo cáo{so_trang}\n\n{dau_bang}\n{than}")
    return slides, {str(i) for i in range(1, len(trang) + 1)}


def build_slides_markdown(
    data: dict[str, Any], params: dict[str, Any], inputs: dict[str, Any] | None = None
) -> tuple[list[str], set[str]]:
    """Nội dung TỪNG SLIDE, do code dựng từ số liệu SQL. Trả (slide, số của code).

    Một chuỗi markdown = một slide. Presenton nhận danh sách này qua
    `slides_markdown`: nó bỏ hẳn bước tự lập dàn ý, chỉ chọn layout và render.
    Nhờ vậy mình giữ được thứ tự, số lượng và nội dung slide, còn phần trình bày
    - thứ code dựng ra xấu - thì giao cho nó.

    Câu hỏi gốc của người dùng không đi kèm: nó chứa những chữ như "cho đẹp",
    "chi tiết vào" - vô hại với một trợ lý nhưng với một bộ sinh nội dung thì đó
    là lời mời thêm thắt.
    """
    if "queries" in data:
        return query_slides(data["queries"], inputs), set()
    inputs = inputs or {}
    period = _period_label(params)
    slides: list[str] = [_slide_bia(data, params, inputs)]
    so_cua_code: set[str] = set()

    for key, ten_mang, mac_dinh in (("personnel", "nhân sự", "personnel"),
                                    ("equipment", "trang thiết bị", "equipment")):
        if not (source := data.get(key)):
            continue
        slides += _slide_chi_tieu(source, ten_mang, period)
        bang, so = _slide_bang(source, mac_dinh, ten_mang, period)
        slides += bang
        so_cua_code |= so
        if (thieu := (source.get("scope") or {}).get("khong_co_chi_tieu")):
            slides[-1] += ("\n\nKhông có số liệu về: " + ", ".join(thieu) + ".")

    if (reporting := data.get("reporting")):
        slides.append(
            f"## Tình hình gửi báo cáo {period}\n\n"
            f"- Đã gửi trong kỳ: {reporting['units_reported']}/"
            f"{reporting['units_total']} đơn vị.\n"
            f"- Chưa gửi: {len(reporting.get('missing') or [])} đơn vị"
            f"{' (danh sách ở slide sau)' if reporting.get('missing') else ''}.")
        bang, so = _slide_don_vi_chua_gui(reporting, period)
        slides += bang
        so_cua_code |= so

    # Cảnh báo chất lượng dữ liệu và ghi chú nguồn là phần người ký cần thấy
    # nhất, nên chúng đứng thành slide riêng chứ không nép vào chân trang.
    cuoi: list[str] = [item["message"] for key in ("personnel", "equipment")
                       for item in (data.get(key) or {}).get("consistency", [])]
    cuoi += sorted({note for key in ("personnel", "equipment")
                    if (note := (data.get(key) or {}).get("scope", {}).get("ghi_chu"))})
    if cuoi:
        slides.append("## Ghi chú và số liệu cần kiểm tra lại\n\n"
                      + "\n".join(f"- {c}" for c in cuoi))

    return slides, so_cua_code


# Lời dặn gửi kèm. Viết bằng tiếng Việt vì bộ slide là tiếng Việt, và viết theo
# lối cấm cụ thể: mỗi dòng ở đây tương ứng một lỗi đã thật sự xảy ra trong bản cũ.
INSTRUCTIONS = (
    "Đây là bộ slide báo cáo hành chính, trình bày trong cuộc họp giao ban. "
    "Toàn bộ nội dung bằng tiếng Việt, văn phong báo cáo, không dùng từ tiếp thị.\n"
    "Mỗi slide đã được soạn sẵn nội dung; việc của bạn là chọn layout và trình "
    "bày lại cho gọn, KHÔNG viết thêm nội dung mới.\n"
    "QUY TẮC BẮT BUỘC:\n"
    "1. Chỉ dùng những con số có sẵn trong nội dung slide. KHÔNG tự cộng, trừ, "
    "tính tỷ lệ hay ước lượng thêm bất kỳ con số nào.\n"
    "2. Chỉ nhắc tên đơn vị, chức vụ, chủng loại có trong nội dung slide, và chỉ "
    "gán cho nó đúng con số ghi kèm. Không nêu tên nào làm ví dụ.\n"
    "3. Không suy diễn nguyên nhân, không dự báo, không đề xuất điều gì mà số "
    "liệu không nói tới.\n"
    "4. Chỉ tiêu nào ghi là không có số liệu thì không được nhận xét về nó.\n"
    "5. Slide có bảng markdown thì BẮT BUỘC chọn layout có bảng. Không chọn "
    "layout biểu đồ, không chọn layout thẻ/gạch đầu dòng, không chuyển bảng "
    "thành câu chữ. Giữ ĐỦ số dòng và số cột của bảng.\n"
    "6. Với layout chỉ tiêu: phần mô tả của mỗi ô PHẢI mở đầu bằng tên chỉ tiêu "
    '(ví dụ "Tổng nhân sự: kỳ trước 27..."). Ô chỉ có con số mà không có tên '
    "chỉ tiêu thì người xem không biết nó là gì.\n"
    "7. CHỈ dùng chữ Latin có dấu tiếng Việt. TUYỆT ĐỐI không dùng chữ Hán, "
    "Nhật, Hàn. Từ Hán-Việt phải viết bằng chữ quốc ngữ: viết \"kiểm tra\", "
    "\"chuyên môn\", \"phát triển\", \"vì sao\" - không được thay bằng ký tự "
    "chữ Hán tương ứng.\n"
    "8. Tiêu đề slide GIỮ NGUYÊN TỪNG CHỮ, kể cả phần đánh số trang dạng "
    '"(2/6)". Không rút gọn, không diễn đạt lại, không bỏ dấu ngoặc. Các slide '
    "cùng một bảng bị cắt trang phải mang tiêu đề giống hệt nhau, chỉ khác số "
    "trang - mỗi trang một cách gọi thì người xem tưởng là sáu bảng khác nhau."
)

# Chỉ dặn nêu mục cảnh báo KHI CÓ cảnh báo. Dặn cứng thì kỳ nào số liệu sạch, bộ
# slide cũng mọc ra một dòng "số liệu cần kiểm tra lại: chưa nêu" - người xem đọc
# vào tưởng hệ thống chưa kiểm, trong khi thật ra không có gì để kiểm.
CANH_BAO_INSTRUCTION = (
    "\n9. Bộ slide phải có phần nêu lại ghi chú và mục số liệu cần kiểm tra lại."
)


def instructions_for(data: dict[str, Any]) -> str:
    co_canh_bao = any((data.get(key) or {}).get("consistency")
                      for key in ("personnel", "equipment"))
    return INSTRUCTIONS + (CANH_BAO_INSTRUCTION if co_canh_bao else "")


def _khong_co_gi_de_trinh_bay(data: dict[str, Any]) -> bool:
    """Không có đơn vị nào và không dòng số liệu nào: dựng deck lúc này chỉ ra một bộ
    slide toàn số 0 kèm "0/0 đơn vị" - trông như báo cáo thật nhưng không nói gì.

    Số liệu từ truy vấn động (`queries`, Text-to-SQL) không qua chốt này: bảng rỗng ở đó tự ghi
    "Không có dữ liệu thỏa điều kiện truy vấn" trên slide (`query_result`)."""
    if "queries" in data:
        return False
    rows = sum(len((data.get(k) or {}).get("breakdown") or []) for k in ("personnel", "equipment"))
    units = (data.get("reporting") or {}).get("units_total") or 0
    return rows == 0 and units == 0


async def brief_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}
    if _khong_co_gi_de_trinh_bay(state["data"]):
        return {"error": "Không tìm thấy đơn vị hay số liệu nào của thuê bao này trong ERP cho kỳ "
                         f"{_period_label(state['params'])}. Kiểm tra lại mã thuê bao (X-Tenant-Id)."}
    slides, so_cua_code = build_slides_markdown(
        state["data"], state["params"], state.get("inputs"))
    logger.info("Đã soạn %d slide để Presenton render", len(slides))
    return {
        "slides_markdown": slides,
        "n_slides": len(slides),
        "so_cua_code": sorted(so_cua_code),
        # Giữ `brief` trong kết quả trả về: đây vẫn là toàn bộ thứ Presenton
        # nhìn thấy, và là chỗ đối chiếu khi trên slide có một con số lạ.
        "brief": "\n\n---\n\n".join(slides),
    }


# --------------------------------------------------------------------------- #
# 2. Dựng file
# --------------------------------------------------------------------------- #
def _output_path(params: dict[str, Any]) -> Path:
    stem = storage.versioned_stem(
        re.sub(r"[^A-Za-z0-9_.-]", "_", f"SLIDE_{params['ky']}"))
    return Path(get_settings().output_dir) / f"{stem}.pptx"


def _fallback_deck(data: dict[str, Any], params: dict[str, Any]) -> DeckSpec:
    """Bộ slide đường lùi: 100% do code dựng, không một chữ nào của LLM."""
    if "queries" in data:
        return query_deck(data["queries"])
    from app.agents.nodes.report import _build_charts

    period = _period_label(params)
    charts = _build_charts(data, params)
    specs: list[SlideSpec] = [
        SlideSpec(kind="title", title=f"Báo cáo số liệu {period}",
                  subtitle="Số liệu trích xuất từ cơ sở dữ liệu nghiệp vụ")
    ]

    for key, ten in (("personnel", "nhân sự"), ("equipment", "trang thiết bị")):
        if not (source := data.get(key)):
            continue
        specs.append(SlideSpec(
            kind="summary", title=f"Chỉ tiêu {ten} {period}",
            metrics=[_metric_box(k, m) for k, m in source["metrics"].items()][:4],
            chart=charts.get(key),
        ))
        if (table := _slide_table(f"{key}_breakdown", data)):
            specs.append(SlideSpec(kind="table", title=f"Chi tiết {ten} theo đơn vị",
                                   table=table))

    if (reporting := data.get("reporting")):
        bullets = [f"{reporting['units_reported']}/{reporting['units_total']} "
                   f"đơn vị đã gửi báo cáo trong kỳ."]
        if (missing := reporting.get("missing")):
            bullets.append("Chưa gửi: " + ", ".join(m["ten_don_vi"] for m in missing) + ".")
        specs.append(SlideSpec(kind="bullet", title="Tình hình gửi báo cáo", bullets=bullets))

    canh_bao = [item["message"] for key in ("personnel", "equipment")
                for item in (data.get(key) or {}).get("consistency", [])]
    if canh_bao:
        specs.append(SlideSpec(kind="bullet", title="Số liệu cần kiểm tra lại",
                               bullets=canh_bao))

    return DeckSpec(title=f"Báo cáo số liệu {period}", subtitle=period, slides=specs)


def _metric_box(key: str, metric: dict[str, Any]) -> MetricBox:
    note = ""
    if metric.get("delta") is not None:
        dau = "+" if metric["delta"] > 0 else ""
        note = f"{dau}{_so(metric['delta'])}"
        if metric.get("delta_pct") is not None:
            note += f" ({_so(metric['delta_pct'])}%)"
    elif metric.get("share_pct") is not None:
        note = f"{_so(metric['share_pct'])}%"
    return MetricBox(label=METRIC_LABELS.get(key, key), value=_so(metric["value"]), note=note)


def _slide_table(data_key: str, data: dict[str, Any]) -> SlideTable | None:
    """Bảng chi tiết dựng theo đúng cột mà tool số liệu khai - xem
    `report.bang_chi_tiet`. Chiều gộp đổi thì nhãn cột đổi theo, không phải sửa
    ở đây."""
    from app.agents.nodes.report import bang_chi_tiet

    key = data_key.removesuffix("_breakdown")
    if key not in ("personnel", "equipment") or not data.get(key):
        return None
    table = bang_chi_tiet(data[key], key)
    return SlideTable(columns=table.columns, rows=table.rows) if table else None


async def _render_local(state: dict[str, Any], output_path: Path, ly_do: Exception) -> dict[str, Any]:
    logger.warning("Presenton không dựng được slide (%s) - dùng bản tự dựng", ly_do)
    spec = await anyio.to_thread.run_sync(
        lambda: _fallback_deck(state["data"], state["params"]))
    path = await anyio.to_thread.run_sync(lambda: build_pptx(spec, output_path))
    return {
        "output_path": str(path),
        "engine": "local",
        "slide_count": count_slides(spec.slides),
        "assumptions": [*state.get("assumptions", []),
                        f"Presenton không dùng được ({ly_do}); bộ slide này do hệ "
                        f"thống tự dựng nên chỉ có số liệu, không có phần nhận xét."],
    }


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    """Dựng slide qua Presenton; hỏng thì lùi về bản code tự dựng, và nói ra."""
    if state.get("error"):
        return {"output_path": ""}

    output_path = _output_path(state["params"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if getattr(get_settings(), "presenton_render_mode", "direct") == "generate":
        return await _render_generate(state, output_path)
    return await _render_direct(state, output_path)


# --------------------------------------------------------------------------- #
# 2a. Dựng thẳng từ layout template - đường mặc định
# --------------------------------------------------------------------------- #
MAX_DECK_ATTEMPTS = 2
MAX_SUMMARY_ATTEMPTS = 3

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["tong_quan", "nhan_xet", "ket_luan"],
    "properties": {
        "tong_quan": {"type": "string", "maxLength": 280},
        "nhan_xet": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["bang", "noi_dung"],
            "properties": {"bang": {"type": "string"}, "noi_dung": {"type": "string", "maxLength": 300}}}},
        "ket_luan": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False, "required": ["tieu_de", "noi_dung"],
            "properties": {"tieu_de": {"type": "string", "maxLength": 22},
                           "noi_dung": {"type": "string", "maxLength": 110}}}},
    },
}
SUMMARY_SYSTEM = ("Bạn là chuyên viên tổng hợp báo cáo số liệu hành chính. Tuyệt đối trung thực "
                  "với dữ liệu. Chỉ viết tiếng Việt bằng chữ quốc ngữ.")


def _summary_prompt(datasets: list[Any], unit: str, period: str) -> str:
    blocks = []
    for d in datasets:
        bang = "\n".join(f"- {cat}: " + ", ".join(f"{name} {so_vn(vals[i])}" for name, vals in d.series)
                         for i, cat in enumerate(d.categories))
        blocks.append(f"### Bảng `{d.key}`: {d.title}\n{bang}\nChỉ tiêu đã tính sẵn:\n"
                      + "\n".join(f"- {f}" for f in d.facts))
    return (f"Báo cáo số liệu {period}. Đơn vị: {unit}.\n\n" + "\n\n".join(blocks)
            + "\n\nViết phần chữ cho bộ slide báo cáo, giọng văn hành chính, ngắn gọn.\n"
            "Quy tắc bắt buộc:\n"
            "- Chỉ dùng con số xuất hiện nguyên văn trong các bảng hoặc chỉ tiêu đã tính sẵn ở trên. "
            "Không tự cộng, trừ, chia, làm tròn hay ước lượng số mới.\n"
            "- Không suy đoán nguyên nhân, không dự báo, không khuyến nghị hay đề xuất việc cần làm: "
            "kết luận chỉ nêu điều số liệu cho thấy.\n"
            "- Mã trạng thái (ví dụ 'Trạng thái 0') giữ nguyên là mã, không tự diễn giải.\n"
            "- `tong_quan`: 1-2 câu, dưới 280 ký tự.\n"
            "- `nhan_xet`: mỗi bảng một mục, trường `bang` ghi đúng khoá bảng, dưới 300 ký tự.\n"
            "- `ket_luan`: đúng 3 ý; `tieu_de` dưới 22 ký tự, `noi_dung` dưới 110 ký tự.")


def _summary_problems(out: dict[str, Any], datasets: list[Any], known: set[str]) -> list[str]:
    text = " ".join([out.get("tong_quan", ""),
                     *(n.get("noi_dung", "") for n in out.get("nhan_xet", [])),
                     *(f"{k.get('tieu_de', '')} {k.get('noi_dung', '')}" for k in out.get("ket_luan", []))])
    problems = []
    if (la := check_numbers(text, known)).unverified:
        problems.append("số không có trong dữ liệu: " + ", ".join(sorted(set(la.unverified))))
    if (chu := tim_chu_ngoai_he(text)):
        problems.append("có ký tự không phải tiếng Việt: " + "".join(sorted(set(chu))))
    if (thieu := {d.key for d in datasets} - {n.get("bang") for n in out.get("nhan_xet", [])}):
        problems.append("thiếu nhận xét cho bảng: " + ", ".join(sorted(thieu)))
    if len(out.get("ket_luan") or []) != 3:
        problems.append("ket_luan phải đúng 3 ý")
    return problems


async def _summarize(datasets: list[Any], unit: str, period: str,
                     known: set[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Qwen viết chữ dẫn dắt; mỗi lần sai quy tắc thì được chỉ ra lỗi và viết lại.

    Trả (bản tóm tắt hoặc None, log). None nghĩa là sau ba lần vẫn sai - bên gọi
    dùng chữ do code tính, không đưa bản sai lên slide.
    """
    from app.services.llm import LLMError, get_llm, parse_json_response

    messages = [{"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": _summary_prompt(datasets, unit, period)}]
    log: dict[str, Any] = {"attempts": []}
    started = time.monotonic()
    for _ in range(MAX_SUMMARY_ATTEMPTS):
        try:
            raw = await get_llm().chat(
                messages, temperature=0.0, max_tokens=1500, thinking=False,
                extra={"response_format": {"type": "json_schema", "json_schema": {
                    "name": "tom_tat_slide", "schema": SUMMARY_SCHEMA, "strict": True}}})
            out = parse_json_response(raw)
        except (LLMError, ValueError) as exc:
            log["attempts"].append({"error": str(exc)[:200]})
            log["llm_error"] = str(exc)[:200]
            break
        problems = _summary_problems(out, datasets, known) if isinstance(out, dict) else ["không phải JSON object"]
        log["attempts"].append({"problems": problems})
        if not problems:
            log["seconds"] = round(time.monotonic() - started, 1)
            return out, log
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Bản trên vi phạm: " + "; ".join(problems)
                      + ". Viết lại toàn bộ JSON đúng quy tắc."}]
    log["seconds"] = round(time.monotonic() - started, 1)
    return None, log


async def _ten_don_vi(inputs: dict[str, Any]) -> str:
    """Tên đơn vị in trên bìa: người dùng nêu thì theo người dùng, không thì tên tenant trong ERP."""
    if (value := str(inputs.get("don_vi_trinh_bay") or "").strip()):
        return value
    from sqlalchemy import text

    from app.core.context import current_tenant_id
    from app.db.erp_session import erp_session_scope

    tenant = current_tenant_id()
    try:
        async with erp_session_scope() as session:
            name = (await session.execute(
                text("SELECT TenancyName FROM AbpTenants WHERE Id = :id"), {"id": tenant})).scalar()
    except Exception as exc:  # noqa: BLE001 - thiếu tên đơn vị không đáng làm hỏng cả bộ slide
        logger.warning("Không tra được tên tenant %s: %s", tenant, exc)
        name = None
    return str(name).strip() if name else f"Đơn vị thuê bao {tenant}"


async def _render_direct(state: dict[str, Any], output_path: Path) -> dict[str, Any]:
    from datetime import date

    from app.documents import slide_deck as sd

    settings = get_settings()
    data, params = state["data"], state["params"]
    timings: dict[str, float] = {}
    assumptions = list(state.get("assumptions", []))
    started = time.monotonic()

    datasets = sd.build_datasets(data, params)
    unit = await _ten_don_vi(state.get("inputs") or {})
    period = _period_label(params)
    report_date = date.today().strftime("%d/%m/%Y")

    # Số hợp lệ cho chữ Qwen: dữ liệu gốc + số kỳ + mọi số code đã viết (tỷ lệ...).
    base_plan = sd.build_plan(data, params, unit, datasets, None, report_date)
    known = (collect_known_numbers(data) | _period_numbers(params) | sd.code_numbers(base_plan)
             | collect_known_numbers([unit, report_date]))

    summary, summary_log = None, {}
    if getattr(settings, "presenton_summary", True) and datasets:
        summary, summary_log = await _summarize(datasets, unit, period, known)
        timings["llm_summary"] = summary_log.get("seconds", 0.0)
        if summary is None and summary_log.get("llm_error"):
            assumptions.append(f"Không gọi được Qwen để viết phần nhận xét ({summary_log['llm_error'][:120]}); "
                               "phần chữ trên slide do hệ thống tính từ số liệu.")
        elif summary is None:
            assumptions.append("Qwen chưa viết được phần nhận xét đúng quy tắc đối chiếu số; "
                               "phần chữ trên slide do hệ thống tính từ số liệu.")
    plan = sd.build_plan(data, params, unit, datasets, summary, report_date) if summary else base_plan
    title = f"Báo cáo số liệu {period} - {unit}"

    presenton = get_presenton()
    deck_check: dict[str, Any] = {}
    try:
        t0 = time.monotonic()
        template = await presenton.get_template()
        timings["presenton_template"] = round(time.monotonic() - t0, 1)
        pid = ""
        for attempt in range(1, MAX_DECK_ATTEMPTS + 1):
            t0 = time.monotonic()
            pid = await presenton.create_blank()
            built = [sd.build_slide(p, i, pid, template) for i, p in enumerate(plan)]
            try:
                await presenton.update_deck(pid, title, template["theme"], [s for s, _ in built])
                deck_check = sd.verify_deck(await presenton.get_deck(pid), plan, [r for _, r in built])
            except PresentonError:
                await presenton.delete_deck(pid)
                raise
            timings["presenton_build"] = round(time.monotonic() - t0, 1)
            deck_check["attempt"] = attempt
            if deck_check["ok"]:
                break
            logger.warning("Deck %s lệch kế hoạch (lần %d): %s", pid, attempt, deck_check["issues"][:5])
            await presenton.delete_deck(pid)
        else:
            raise PresentonError("deck Presenton lưu lệch so với nội dung đã soạn sau "
                                 f"{MAX_DECK_ATTEMPTS} lần: {deck_check['issues'][:3]}")
        t0 = time.monotonic()
        try:
            content = await presenton.export_deck(pid, "pptx")
        except PresentonError:
            await presenton.delete_deck(pid)  # không để lại deck mồ côi trên Presenton
            raise
        timings["presenton_export"] = round(time.monotonic() - t0, 1)
    except (PresentonError, sd.DeckBuildError) as exc:
        result = await _render_local(state, output_path, exc)
        result["timings"] = timings
        return result

    await anyio.to_thread.run_sync(lambda: output_path.write_bytes(content))
    timings["render_total"] = round(time.monotonic() - started, 1)
    logger.info("Presenton (dựng thẳng) xong %d slide sau %.1fs, deck=%s, nhận xét=%s: %s",
                len(plan), timings["render_total"], pid, "qwen" if summary else "code", output_path)
    return {
        "output_path": str(output_path),
        "engine": "presenton",
        "presentation_id": pid,
        "edit_url": f"/presentation?id={pid}",
        "elapsed_seconds": timings["render_total"],
        "slide_count": len(plan),
        "so_cua_code": sorted(sd.code_numbers(plan) | collect_known_numbers([unit, report_date])),
        "summary_source": "qwen" if summary else "code",
        "deck_check": {**deck_check, "summary": summary_log},
        "timings": timings,
        "assumptions": assumptions,
        "brief": "\n\n---\n\n".join(
            "\n".join(filter(None, [f"[{p.role}] {p.layout}", *p.texts.values(),
                                    *(f"{i.get('card_title', i.get('entry_title', ''))}: "
                                      f"{i.get('card_description', i.get('card_caption', i.get('entry_description', '')))}"
                                      for items in p.lists.values() for i in items)]))
            for p in plan),
    }


# --------------------------------------------------------------------------- #
# 2b. Để Presenton tự sinh (đường cũ, PRESENTON_RENDER_MODE=generate)
# --------------------------------------------------------------------------- #
async def _render_generate(state: dict[str, Any], output_path: Path) -> dict[str, Any]:
    try:
        deck = await get_presenton().generate(
            state["brief"], n_slides=state["n_slides"],
            slides_markdown=state["slides_markdown"],
            instructions=instructions_for(state["data"]))
    except PresentonError as exc:
        return await _render_local(state, output_path, exc)

    await anyio.to_thread.run_sync(lambda: output_path.write_bytes(deck.content))
    logger.info("Presenton render xong %d slide sau %.0fs: %s",
                state["n_slides"], deck.elapsed_seconds, output_path)
    return {
        "output_path": str(output_path),
        "engine": "presenton",
        "presentation_id": deck.presentation_id,
        "edit_url": deck.edit_path,
        "elapsed_seconds": deck.elapsed_seconds,
    }


# --------------------------------------------------------------------------- #
# 3. Đối chiếu số TRONG FILE đã dựng
# --------------------------------------------------------------------------- #
def doc_text_slide(path: str | Path) -> tuple[int, list[tuple[int, str, str]]]:
    """(số slide, [(thứ tự slide, tiêu đề, một đoạn chữ)]) - kể cả chữ trong bảng.

    Số slide đếm từ chính file: nhãn "N slide" trên giao diện từng lệch với file
    thật khi bảng dài bị tách trang, và người dùng mở file ra là thấy ngay.
    """
    from pptx import Presentation

    slides = list(Presentation(str(path)).slides)
    ket_qua: list[tuple[int, str, str]] = []
    for index, slide in enumerate(slides, start=1):
        khung = [sh.text_frame.text for sh in slide.shapes
                 if sh.has_text_frame and sh.text_frame.text.strip()]
        tieu_de = (khung[0].strip().splitlines()[0] if khung else f"Slide {index}")[:80]
        for text in khung:
            for dong in text.splitlines():
                if dong.strip():
                    ket_qua.append((index, tieu_de, dong.strip()))
        for shape in slide.shapes:
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if cell.text.strip():
                            ket_qua.append((index, tieu_de, cell.text.strip()))
    return len(slides), ket_qua


async def verify_node(state: dict[str, Any]) -> dict[str, Any]:
    """Đọc lại file vừa dựng và soi từng con số trong đó.

    Kiểm TRÊN FILE chứ không trên markdown đã gửi đi: Presenton viết lại chữ khi
    dựng slide, nên thứ duy nhất đáng tin để kiểm là thứ đã nằm trong file.

    Số không truy được về dữ liệu gốc là CẢNH BÁO chứ không chặn xuất file: file
    đã dựng xong ở phía Presenton, xoá đi thì người dùng không còn gì để sửa. Bù
    lại kết quả trả về nói rõ từng con số đáng ngờ nằm ở slide nào.
    """
    if state.get("error") or not state.get("output_path"):
        return {"validation": {"status": "skipped", "issues": []}}

    data = state.get("data", {})
    numeric_source = [{"rows": q["rows"], "question": q["question"], "columns": q["columns"]}
                      for q in data["queries"]] if "queries" in data else data
    known = collect_known_numbers(numeric_source) | _period_numbers(state.get("params", {}))

    try:
        slide_count, doan_van = await anyio.to_thread.run_sync(
            lambda: doc_text_slide(state["output_path"]))
    except Exception as exc:  # noqa: BLE001 - không đọc lại được thì nói thẳng là chưa kiểm
        logger.warning("Không đọc lại được file slide để đối chiếu: %s", exc)
        return {"validation": {"status": "skipped", "issues": [],
                               "ghi_chu": "Chưa đối chiếu được số trong file"}}

    # Số do CODE viết ra cũng là số thật: số trang của bảng đã cắt ("2/6").
    known |= set(state.get("so_cua_code") or [])

    issues: list[dict[str, Any]] = []
    for index, tieu_de, dong in doan_van:
        check = check_numbers(dong, known, tieu_de)
        if not check.ok:
            issues.append({"type": "unverified_number", "section": tieu_de,
                           "numbers": check.unverified, "severity": "warning",
                           "quote": dong[:160]})
        # Chữ thuộc hệ chữ khác lọt vào giữa câu tiếng Việt. Cảnh báo chứ không
        # chặn, cùng lý do với số không truy được: file đã dựng xong ở phía
        # Presenton, xoá đi thì người dùng không còn gì để sửa. Bù lại phải nói
        # rõ slide nào và ký tự nào, vì hai ký tự lạ giữa một câu dài thì đọc
        # lướt không thấy.
        if (la := tim_chu_ngoai_he(dong)):
            issues.append({"type": "foreign_script", "section": tieu_de,
                           "slide": index, "characters": sorted(set(la)),
                           "severity": "warning", "quote": dong[:160]})

    return {
        "slide_count": slide_count,
        "validation": {
            "status": "warning" if issues else "passed",
            "issues": issues,
            "checked_numbers": len(known),
        },
    }
