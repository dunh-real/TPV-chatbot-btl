"""Các node của workflow 5: tạo bộ slide.

    yêu cầu ─> tham số ─> số liệu (SQL, dùng chung workflow 4)
                       └─> bản tóm tắt số liệu (CODE dựng, không qua LLM)
                            └─> Presenton dựng .pptx ─> đối chiếu số trong file

VÌ SAO ĐỔI SANG PRESENTON

Bản cũ để LLM tự lập dàn ý rồi tự viết từng gạch đầu dòng. Chạy ba lần liên tiếp
trên cùng một câu hỏi thì cả ba lần đều có câu bịa: "Phòng Kinh doanh và Kỹ thuật
thiếu số liệu" (tên đơn vị model tự chọn), "8 đơn vị thiếu dữ liệu nhân sự" (danh
sách thật có 9). Van chắn số không bắt được vì 8 trùng với tháng báo cáo.

Nên phần sinh nội dung giao cho Presenton, và phần khó nhất - biết con số nào là
thật - giữ nguyên ở đây: tóm tắt số liệu do CODE dựng từ kết quả SQL, model chỉ
được bày lại thứ đã có trong đó.

BA THỨ KHÔNG ĐỔI SO VỚI BẢN CŨ

  1. Số liệu do `app.tools.data` tính, không con số nào do model tính.
  2. Bộ slide trả về vẫn bị đối chiếu số một lần nữa (`verify_node`).
  3. Presenton hỏng thì vẫn ra file: đường lùi dựng bằng `pptx_builder`, và
     đường lùi đó KHÔNG gọi LLM - chỉ ô chỉ tiêu, biểu đồ, bảng và ghi chú do
     code viết. Thà bộ slide khô khan còn hơn bộ slide có câu bịa.
"""

from __future__ import annotations

import logging
import re
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
from app.documents.verify import check_numbers, collect_known_numbers
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
        slides.append(f"## Chi tiết {ten_mang} theo {str(nhan).lower()}{so_trang}\n\n"
                      f"{dau_bang}\n{than}")

    # Số trang do CODE viết ra, không phải model bịa - bước đối chiếu số phải
    # biết điều đó, nếu không "(2/6)" thành hai con số không truy được.
    so_cua_code = {str(index) for index in range(1, len(trang) + 1)}
    return slides, so_cua_code


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
    inputs = inputs or {}
    period = _period_label(params)
    slides: list[str] = [_slide_bia(data, params, inputs)]
    so_cua_code: set[str] = set()

    for key, ten_mang, mac_dinh in (("personnel", "quân số", "personnel"),
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
        dong = [f"- Đã gửi trong kỳ: {reporting['units_reported']}/"
                f"{reporting['units_total']} đơn vị."]
        if (missing := reporting.get("missing")):
            dong.append("- Chưa gửi: "
                        + ", ".join(m["ten_don_vi"] for m in missing) + ".")
        slides.append(f"## Tình hình gửi báo cáo {period}\n\n" + "\n".join(dong))

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
    '(ví dụ "Tổng quân số: kỳ trước 27..."). Ô chỉ có con số mà không có tên '
    "chỉ tiêu thì người xem không biết nó là gì."
)

# Chỉ dặn nêu mục cảnh báo KHI CÓ cảnh báo. Dặn cứng thì kỳ nào số liệu sạch, bộ
# slide cũng mọc ra một dòng "số liệu cần kiểm tra lại: chưa nêu" - người xem đọc
# vào tưởng hệ thống chưa kiểm, trong khi thật ra không có gì để kiểm.
CANH_BAO_INSTRUCTION = (
    "\n6. Bộ slide phải có phần nêu lại ghi chú và mục số liệu cần kiểm tra lại."
)


def instructions_for(data: dict[str, Any]) -> str:
    co_canh_bao = any((data.get(key) or {}).get("consistency")
                      for key in ("personnel", "equipment"))
    return INSTRUCTIONS + (CANH_BAO_INSTRUCTION if co_canh_bao else "")


async def brief_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}
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
    from app.agents.nodes.report import _build_charts

    period = _period_label(params)
    charts = _build_charts(data, params)
    specs: list[SlideSpec] = [
        SlideSpec(kind="title", title=f"Báo cáo số liệu {period}",
                  subtitle="Số liệu trích xuất từ cơ sở dữ liệu nghiệp vụ")
    ]

    for key, ten in (("personnel", "quân số"), ("equipment", "trang thiết bị")):
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


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    """Presenton dựng slide; hỏng thì lùi về bản code tự dựng, và nói ra."""
    if state.get("error"):
        return {"output_path": ""}

    output_path = _output_path(state["params"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        deck = await get_presenton().generate(
            state["brief"], n_slides=state["n_slides"],
            slides_markdown=state["slides_markdown"],
            instructions=instructions_for(state["data"]))
    except PresentonError as exc:
        logger.warning("Presenton không dựng được slide (%s) - dùng bản tự dựng", exc)
        spec = await anyio.to_thread.run_sync(
            lambda: _fallback_deck(state["data"], state["params"]))
        path = await anyio.to_thread.run_sync(lambda: build_pptx(spec, output_path))
        return {
            "output_path": str(path),
            "engine": "local",
            "slide_count": count_slides(spec.slides),
            "assumptions": [f"Presenton không dùng được ({exc}); bộ slide này do hệ "
                            f"thống tự dựng nên chỉ có số liệu, không có phần nhận xét."],
        }

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
    known = collect_known_numbers(data) | _period_numbers(state.get("params", {}))

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
    for _index, tieu_de, dong in doan_van:
        check = check_numbers(dong, known, tieu_de)
        if not check.ok:
            issues.append({"type": "unverified_number", "section": tieu_de,
                           "numbers": check.unverified, "severity": "warning",
                           "quote": dong[:160]})

    return {
        "slide_count": slide_count,
        "validation": {
            "status": "warning" if issues else "passed",
            "issues": issues,
            "checked_numbers": len(known),
        },
    }
