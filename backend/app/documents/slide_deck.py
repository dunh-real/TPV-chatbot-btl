"""Bộ slide dựng thẳng từ layout của template Presenton - Presenton chỉ render và xuất file.

    số liệu SQL ─> bộ dữ liệu (code) ─> kế hoạch deck: layout + mọi chữ, số, biểu đồ, bảng
                                      └─> [Qwen viết tổng quan, nhận xét, kết luận - có kiểm số]
               ─> ui từng slide (layout template đã điền) ─> Presenton update + export

VÌ SAO KHÔNG ĐỂ PRESENTON TỰ SINH NỮA

Đường `generate/async` để LLM của Presenton chọn layout và viết lại từng slide. Thực
nghiệm `report_pipeline` (10 câu hỏi, 09/10/2026) cho thấy khoảng 25% deck vẫn hỏng
dù nội dung đã soạn sẵn: tự vẽ biểu đồ với số sai, mất danh mục, lẫn chữ Hán, cắt cụt
tiêu đề. Dựng thẳng từ layout thì cả 20/20 deck đạt, biểu đồ khớp SQL 17/17, và nhanh
hơn vì không còn lượt LLM bên Presenton.

Module này thuần dữ liệu - không gọi mạng. Gọi Presenton ở `app.services.presenton`,
gọi Qwen ở `app.agents.nodes.presentation`.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.documents.verify import collect_known_numbers, tim_chu_ngoai_he

PALETTE = ["#234CD9", "#93B4F5", "#F59E0B", "#10B981", "#6B7280"]
ICON = "/static/icons/bold/{}-bold.svg"
# Tắt sinh ảnh nên ô ảnh dùng ảnh giữ chỗ trung tính có sẵn trong Presenton.
PLACEHOLDER_IMAGE = "/static/images/placeholder.jpg"

# Bảng chi tiết cắt trang ở đây, KHÔNG cắt bớt dòng: mỗi dòng đều lên slide.
TABLE_ROWS_PER_SLIDE = 10
# Biểu đồ nhiều cột hơn thì renderer ẩn bớt nhãn; phần còn lại gộp vào "Các mục khác"
# và vẫn có đủ trong bảng chi tiết ngay sau.
CHART_MAX_BARS = 12
# Renderer ẩn bớt nhãn trục hoành khi nhiều danh mục: từ ngưỡng này vẽ cột ngang.
HORIZONTAL_FROM = 4

LAYOUT_TITLE = "title_slide"
LAYOUT_TOC = "table_of_contents"
LAYOUT_CARDS = "title_description_bullet_points_list_with_icon"   # đúng 4 thẻ
LAYOUT_GRID = "title_description_bullet_points_grid_with_icon"    # 2-6 thẻ
LAYOUT_CHART = "title_description_chart"
LAYOUT_TABLE_BASE = "title_description_chart_table"


class DeckBuildError(ValueError):
    """Kế hoạch deck không khớp layout (số thẻ ngoài giới hạn, thiếu layout...)."""


# --------------------------------------------------------------------------- #
# Định dạng số
# --------------------------------------------------------------------------- #
def so(value: Any) -> str:
    """Số kiểu Việt Nam: 1.234 và 31,3."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            value = int(value)
        else:
            return f"{value:.1f}".replace(".", ",")
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    return str(value)


def ty_le(part: float, total: float) -> str:
    return so(round(100 * part / total, 1)) + "%" if total else "-"


def _dau(delta: float) -> str:
    return ("+" if delta > 0 else "") + so(delta)


def _nhan(text: Any) -> str:
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------- #
# Bộ dữ liệu: số liệu SQL đổi sang dạng biểu đồ + bảng
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Dataset:
    key: str                    # "personnel" | "equipment"
    title: str                  # "Nhân sự theo đơn vị"
    short: str                  # tiêu đề slide, ngắn
    group_label: str            # "Đơn vị" | "Chức vụ" | "Chủng loại"
    unit: str                   # "người" | "thiết bị"
    categories: list[str]
    series: list[tuple[str, list[float]]]
    table_columns: list[str]
    table_rows: list[list[str]]
    facts: list[str] = field(default_factory=list)
    highlights: list[tuple[str, str]] = field(default_factory=list)
    grouped_others: int = 0     # số mục đã gộp vào "Các mục khác" trên biểu đồ

    @property
    def chart_type(self) -> str:
        return "horizontal_bar" if len(self.categories) >= HORIZONTAL_FROM else "bar"


def canh_bao_ky(source: dict[str, Any]) -> str:
    """Câu đầu ghi chú của tool khi kỳ hỏi KHÔNG có dữ liệu và số trả về là hiện trạng.

    Ví dụ "Kỳ 2020-01 chưa có dữ liệu thiết bị trong ERP." - phải đứng trước mọi con số,
    không thì "194 thiết bị" đọc như số của kỳ báo cáo.
    """
    ghi_chu = str((source.get("scope") or {}).get("ghi_chu") or "")
    if "chưa có dữ liệu" not in ghi_chu and "HIỆN TRẠNG" not in ghi_chu:
        return ""
    return ghi_chu.split(". ")[0].rstrip(".") + "."


def _period(params: dict[str, Any]) -> str:
    return f"tháng {params['thang']}/{params['nam']}"


def _compare(params: dict[str, Any]) -> str:
    ky = str(params.get("compare_to") or "")
    return f"tháng {int(ky[5:])}/{ky[:4]}" if len(ky) == 7 and "-" in ky else ""


def _cot(source: dict[str, Any], key: str) -> list[dict[str, str]]:
    """Cột của bảng chi tiết theo đúng tool khai; số liệu cũ không khai thì theo mặc định WF4."""
    from app.agents.nodes.report import _COT_MAC_DINH

    return source.get("breakdown_columns") or _COT_MAC_DINH[key]


def _table(source: dict[str, Any], key: str) -> tuple[list[str], list[list[str]]]:
    columns = _cot(source, key)
    rows = [[_nhan(so(row.get(c["key"], "")) if isinstance(row.get(c["key"]), (int, float))
                   else row.get(c["key"], "")) for c in columns]
            for row in source.get("breakdown") or []]
    return [c["label"] for c in columns], rows


def _gop_bieu_do(pairs: list[tuple[str, float]], ten_khac: str) -> tuple[list[tuple[str, float]], int]:
    if len(pairs) <= CHART_MAX_BARS:
        return pairs, 0
    giu, con = pairs[:CHART_MAX_BARS - 1], pairs[CHART_MAX_BARS - 1:]
    return giu + [(ten_khac, sum(v for _, v in con))], len(con)


def _personnel(source: dict[str, Any], params: dict[str, Any]) -> Dataset:
    group = _cot(source, "personnel")[0]["label"]
    period, compare = _period(params), _compare(params)
    rows = sorted(source.get("breakdown") or [], key=lambda r: -(r.get("nhan_su") or 0))
    names = [_nhan(r.get("ten_nhom") or r.get("ten_don_vi")) for r in rows]
    cur = [r.get("nhan_su") or 0 for r in rows]
    series = [(period.capitalize(), cur)]
    if compare and any("nhan_su_ky_truoc" in r for r in rows):
        series.append((compare.capitalize(), [r.get("nhan_su_ky_truoc") or 0 for r in rows]))
    series = [s for s in series if any(s[1])] or series[:1]

    columns = [c["label"] for c in _cot(source, "personnel")]
    keys = [c["key"] for c in _cot(source, "personnel")]
    table_rows = [[_nhan(so(r.get(k)) if isinstance(r.get(k), (int, float)) else r.get(k, "")) for k in keys]
                  for r in rows]

    metrics = source.get("metrics") or {}
    total = (metrics.get("total_personnel") or {}).get("value", sum(cur))
    ds = Dataset(key="personnel", title=f"Nhân sự theo {group.lower()}", short="Nhân sự",
                 group_label=group, unit="người", categories=names, series=series,
                 table_columns=columns, table_rows=table_rows)

    tp = metrics.get("total_personnel") or {}
    facts = [f"Tổng nhân sự {period}: {so(total)} người"
             + (f", kỳ trước {so(tp['prev'])} ({_dau(tp['delta'])})" if tp.get("prev") is not None
                and tp.get("delta") is not None else "") + "."]
    if rows and total:
        top = rows[0]
        facts.append(f"Đông nhất: {names[0]} với {so(top.get('nhan_su') or 0)} người, "
                     f"chiếm {ty_le(top.get('nhan_su') or 0, total)} tổng nhân sự.")
    hires = (metrics.get("new_hires") or {}).get("value")
    quits = (metrics.get("resignations") or {}).get("value")
    if hires is not None or quits is not None:
        facts.append(f"Trong kỳ: {so(hires or 0)} tuyển mới, {so(quits or 0)} nghỉ việc.")
    zero = [n for n, v in zip(names, cur) if not v]
    if zero:
        facts.append(f"{len(zero)}/{len(names)} {group.lower()} chưa có nhân sự đang làm việc.")
    ds.facts = facts

    cards = [("Tổng nhân sự", f"{so(total)} người trên {len(names)} {group.lower()}"
              + (f"; kỳ trước {so(tp['prev'])} ({_dau(tp['delta'])})." if tp.get("prev") is not None
                 and tp.get("delta") is not None else "."))]
    if rows and total:
        cards.append(("Đông nhất", f"{names[0]}: {so(cur[0])} người, chiếm {ty_le(cur[0], total)}."))
    cards.append(("Tuyển mới, nghỉ việc", f"{so(hires or 0)} người tuyển mới, {so(quits or 0)} người nghỉ việc trong kỳ."))
    nonzero = [(n, v) for n, v in zip(names, cur) if v]
    if zero:
        cards.append((f"{group} chưa có nhân sự", f"{len(zero)} {group.lower()}: "
                      + ", ".join(zero[:3]) + (" ..." if len(zero) > 3 else "") + "."))
    elif nonzero:
        cards.append(("Ít nhất", f"{nonzero[-1][0]}: {so(nonzero[-1][1])} người."))
    ds.highlights = cards
    return ds


def _equipment(source: dict[str, Any], params: dict[str, Any]) -> Dataset:
    group = _cot(source, "equipment")[0]["label"]
    period = _period(params)
    scope = source.get("scope") or {}
    totals: dict[str, float] = {}
    for row in source.get("breakdown") or []:
        name = _nhan(row.get("ten_nhom") or row.get("ten_don_vi") or row.get("chung_loai"))
        totals[name] = totals.get(name, 0) + (row.get("so_luong") or 0)
    # Thiết bị chưa gán đơn vị đã có sẵn trong breakdown thành dòng "(chưa gán ...)":
    # KHÔNG cộng thêm `chua_gan_don_vi` vào biểu đồ, cộng là đếm trùng.
    unassigned = scope.get("chua_gan_don_vi") or 0
    pairs = sorted(totals.items(), key=lambda kv: -kv[1])
    # Nhóm giả "(chưa gán ...)" không phải một đơn vị/chủng loại: không đem ra xếp hạng.
    ranked = [kv for kv in pairs if not kv[0].startswith("(")]
    chart_pairs, others = _gop_bieu_do(pairs, f"Các {group.lower()} khác")

    columns, table_rows = _table(source, "equipment")
    metrics = source.get("metrics") or {}
    te = metrics.get("total_equipment") or {}
    total = te.get("value", sum(totals.values()))
    types = (metrics.get("equipment_types") or {}).get("value")
    ds = Dataset(key="equipment", title=f"Trang thiết bị theo {group.lower()}", short="Trang thiết bị",
                 group_label=group, unit="thiết bị", categories=[n for n, _ in chart_pairs],
                 series=[("Số lượng", [v for _, v in chart_pairs])],
                 table_columns=columns, table_rows=table_rows, grouped_others=others)

    delta = (f", kỳ trước {so(te['prev'])} ({_dau(te['delta'])})"
             if te.get("prev") is not None and te.get("delta") is not None else "")
    facts = [f"Tổng trang thiết bị {period}: {so(total)}{delta}."]
    if (canh_bao := canh_bao_ky(source)):
        facts = [f"Lưu ý: {canh_bao} Số dưới đây là hiện trạng, không phải số của kỳ.",
                 f"Tổng trang thiết bị theo hiện trạng: {so(total)}."]
    if ranked and total:
        facts.append(f"Nhiều nhất: {ranked[0][0]} với {so(ranked[0][1])}, chiếm {ty_le(ranked[0][1], total)}.")
    if types is not None:
        facts.append(f"Số loại thiết bị khác nhau: {so(types)}.")
    if unassigned:
        facts.append(f"{so(unassigned)}/{so(total)} thiết bị chưa gán đơn vị trong ERP.")
    ds.facts = facts

    cards = [("Tổng trang thiết bị", f"{so(total)} thiết bị{delta}.")]
    if ranked and total:
        cards.append(("Nhiều nhất", f"{ranked[0][0]}: {so(ranked[0][1])} thiết bị, chiếm {ty_le(ranked[0][1], total)}."))
    if types is not None:
        cards.append(("Số loại thiết bị", f"{so(types)} tên thiết bị khác nhau trong kỳ."))
    if unassigned:
        cards.append(("Chưa gán đơn vị", f"{so(unassigned)}/{so(total)} thiết bị chưa gán phòng ban trong ERP."))
    elif len(ranked) > 1:
        cards.append(("Ít nhất", f"{ranked[-1][0]}: {so(ranked[-1][1])} thiết bị."))
    ds.highlights = cards
    return ds


def build_datasets(data: dict[str, Any], params: dict[str, Any]) -> list[Dataset]:
    out: list[Dataset] = []
    if (source := data.get("personnel")) and source.get("breakdown"):
        out.append(_personnel(source, params))
    if (source := data.get("equipment")) and source.get("breakdown"):
        out.append(_equipment(source, params))
    return out


# --------------------------------------------------------------------------- #
# Thẻ nội dung do code viết
# --------------------------------------------------------------------------- #
def kpi_cards(data: dict[str, Any]) -> list[tuple[str, str]]:
    """(giá trị, chú thích) cho slide "Chỉ tiêu chính" - lấy nguyên chỉ tiêu tool đã tính."""
    from app.agents.nodes.report import METRIC_LABELS

    units = {"total_personnel": "người", "new_hires": "người", "resignations": "người",
             "total_equipment": "thiết bị", "equipment_types": "loại"}
    cards: list[tuple[str, str]] = []
    for key in ("personnel", "equipment"):
        for name, m in ((data.get(key) or {}).get("metrics") or {}).items():
            if m.get("value") is None:
                continue
            caption = METRIC_LABELS.get(name, name)
            if m.get("delta") is not None and m.get("prev") is not None:
                caption += f"; kỳ trước {so(m['prev'])} ({_dau(m['delta'])})"
            elif m.get("share_pct") is not None:
                caption += f"; bằng {so(m['share_pct'])}% tổng nhân sự"
            cards.append((f"{so(m['value'])} {units.get(name, '')}".strip(), caption))
    return cards[:6]


def scope_cards(data: dict[str, Any], params: dict[str, Any], unit: str) -> list[tuple[str, str]]:
    sources = [s.get("scope", {}).get("nguon") for k in ("personnel", "equipment")
               if (s := data.get(k)) and s.get("scope", {}).get("nguon")]
    notes = sorted({n for k in ("personnel", "equipment")
                    if (n := ((data.get(k) or {}).get("scope") or {}).get("ghi_chu"))})
    compare = _compare(params)
    return [
        ("Đơn vị báo cáo", unit),
        ("Nguồn dữ liệu", "CSDL ERP, truy vấn chỉ đọc"
         + (f" ({', '.join(sources)})." if sources else ".")),
        ("Kỳ báo cáo", _period(params).capitalize() + (f", đối chiếu {compare}." if compare else ".")),
        ("Quy ước số liệu", " ".join(notes) or "Đếm theo bản ghi chưa xoá trong ERP."),
    ]


def note_cards(data: dict[str, Any]) -> list[tuple[str, str]]:
    """Đúng 4 thẻ: cảnh báo dữ liệu thật trước, thiếu thì bổ sung ghi chú chung."""
    cards: list[tuple[str, str]] = []
    for key in ("personnel", "equipment"):
        if (canh_bao := canh_bao_ky(data.get(key) or {})):
            cards.append(("Số liệu không theo kỳ", canh_bao + " Số trên slide là hiện trạng."))
    for key in ("personnel", "equipment"):
        source = data.get(key) or {}
        for item in source.get("consistency") or []:
            cards.append(("Số liệu cần kiểm tra lại", item["message"]))
        scope = source.get("scope") or {}
        if (thieu := [t for t in scope.get("khong_co_chi_tieu") or [] if not t.startswith("(")]):
            ten = "nhân sự" if key == "personnel" else "thiết bị"
            cards.append((f"Chỉ tiêu {ten} chưa có nguồn", "Chưa có số liệu về: " + ", ".join(thieu) + "."))
        if scope.get("units_missing_count"):
            ten = "nhân sự" if key == "personnel" else "thiết bị"
            cards.append((f"Đơn vị chưa có dữ liệu {ten}",
                          f"{so(scope['units_missing_count'])}/{so(scope.get('units_requested') or 0)} "
                          f"đơn vị không có bản ghi {ten} trong kỳ."))
    generic = [("Dữ liệu tổng hợp", "Chỉ dùng số đếm và tổng theo nhóm, không chứa thông tin cá nhân."),
               ("Truy vấn chỉ đọc", "Số liệu lấy bằng tài khoản chỉ đọc, không thay đổi CSDL nguồn."),
               ("Kiểm chứng", "Mọi con số trên slide đã được đối chiếu tự động với kết quả truy vấn."),
               ("Thời điểm số liệu", "Dữ liệu nhập sau thời điểm lập báo cáo sẽ làm thay đổi kết quả.")]
    return (list(dict.fromkeys(cards)) + generic)[:4]


# --------------------------------------------------------------------------- #
# Kế hoạch deck
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SlidePlan:
    layout: str
    role: str
    note: str
    texts: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    icons: list[str] = field(default_factory=list)
    dataset: Dataset | None = None
    table: tuple[list[str], list[list[str]]] | None = None


def _trang(rows: list[list[str]]) -> list[list[list[str]]]:
    return [rows[i:i + TABLE_ROWS_PER_SLIDE] for i in range(0, len(rows), TABLE_ROWS_PER_SLIDE)] or [[]]


def _table_slides(title: str, columns: list[str], rows: list[list[str]], intro: str) -> list[SlidePlan]:
    pages = _trang(rows)
    out = []
    for i, page in enumerate(pages, start=1):
        so_trang = f" ({i}/{len(pages)})" if len(pages) > 1 else ""
        out.append(SlidePlan(layout=LAYOUT_TABLE_BASE, role="table", note=f"{title}{so_trang}: {intro}",
                             texts={"main_heading": f"{title}{so_trang}", "intro_paragraph": intro},
                             table=(columns, page)))
    return out


def build_plan(data: dict[str, Any], params: dict[str, Any], unit: str,
               datasets: list[Dataset], summary: dict[str, Any] | None,
               report_date: str) -> list[SlidePlan]:
    """Toàn bộ nội dung deck. Không chữ nào trên slide nằm ngoài kế hoạch này."""
    period = _period(params)
    notes = {n["bang"]: n["noi_dung"] for n in (summary or {}).get("nhan_xet", [])}
    overview = (summary or {}).get("tong_quan") or (
        f"Báo cáo số liệu {period} của {unit}: " + "; ".join(d.title.lower() for d in datasets) + ".")
    reporting = data.get("reporting") or {}

    sections = [("Phạm vi và nguồn dữ liệu", "Đơn vị, nguồn, kỳ báo cáo, quy ước"),
                ("Chỉ tiêu chính", "Các con số tổng hợp của kỳ")]
    sections += [(d.short, d.title) for d in datasets]
    if reporting:
        sections.append(("Tình hình gửi báo cáo", "Đơn vị đã gửi và chưa gửi trong kỳ"))
    sections.append(("Ghi chú dữ liệu", "Điểm cần lưu ý khi đọc số liệu"))
    if summary:
        sections.append(("Kết luận", "Ba điểm chính rút ra từ số liệu"))

    plan: list[SlidePlan] = [
        SlidePlan(layout=LAYOUT_TITLE, role="title", note=overview, texts={
            "primary_heading": f"Báo cáo T{params['thang']}/{params['nam']}",
            "supporting_paragraph": overview,
            "badge_initials": "".join(w[0] for w in unit.split()[:2]).upper() or "BC",
            "card_primary_text": unit,
            "card_secondary_text": f"CSDL ERP · {report_date}"}),
        SlidePlan(layout=LAYOUT_TOC, role="toc", note="Nội dung trình bày.",
                  texts={"header_text": "Nội dung trình bày"},
                  lists={"entry_grid": [{"badge_number": str(i), "entry_title": t, "entry_description": d}
                                        for i, (t, d) in enumerate(sections, start=1)]}),
        SlidePlan(layout=LAYOUT_CARDS, role="scope", note="Phạm vi và nguồn dữ liệu.",
                  texts={"section_heading": "Phạm vi và nguồn dữ liệu",
                         "body_paragraph": f"Bộ slide tổng hợp số liệu {period} trực tiếp từ CSDL ERP."},
                  lists={"card_stack": [{"card_title": t, "card_description": d}
                                        for t, d in scope_cards(data, params, unit)]},
                  icons=["buildings", "database", "calendar", "ruler"]),
    ]
    kpis = kpi_cards(data)
    if len(kpis) >= 2:
        plan.append(SlidePlan(layout=LAYOUT_GRID, role="kpi", note="; ".join(f"{c}: {v}" for v, c in kpis),
                              texts={"large_heading": "Chỉ tiêu chính",
                                     "supporting_paragraph": f"Các chỉ tiêu tổng hợp {period}, "
                                                             "tính trực tiếp từ dữ liệu ERP."},
                              lists={"grid_1": [{"card_title": v, "card_caption": c} for v, c in kpis]},
                              icons=["users", "chart-bar", "user-plus", "user-minus", "package", "list"]))

    for d in datasets:
        body = notes.get(d.key) or " ".join(d.facts)
        plan.append(SlidePlan(layout=LAYOUT_CHART, role="chart", dataset=d, note=body,
                              texts={"primary_heading": d.title, "supporting_paragraph": body}))
        plan.append(SlidePlan(layout=LAYOUT_CARDS, role="highlights", dataset=d,
                              note="; ".join(f"{t}: {x}" for t, x in d.highlights),
                              texts={"section_heading": f"{d.short}: điểm nổi bật",
                                     "body_paragraph": f"Các điểm chính về {d.title.lower()} {period}."},
                              lists={"card_stack": [{"card_title": t, "card_description": x}
                                                    for t, x in _du_bon(d.highlights)]},
                              icons=["sigma", "arrow-up", "arrows-left-right", "warning-circle"]))
        intro = (f"Chi tiết {d.title.lower()} {period}, {len(d.table_rows)} dòng, "
                 f"đúng theo kết quả truy vấn ERP.")
        plan += _table_slides(f"Chi tiết {d.short.lower()}", d.table_columns, d.table_rows, intro)

    if reporting:
        missing = reporting.get("missing") or []
        cards = [("Đã gửi báo cáo", f"{so(reporting.get('units_reported', 0))}/"
                                    f"{so(reporting.get('units_total', 0))} đơn vị đã gửi trong kỳ."),
                 ("Chưa gửi báo cáo", f"{so(len(missing))} đơn vị"
                                      + (" (danh sách ở slide sau)." if missing else "."))]
        plan.append(SlidePlan(layout=LAYOUT_GRID, role="reporting", note="; ".join(f"{t}: {x}" for t, x in cards),
                              texts={"large_heading": "Tình hình gửi báo cáo",
                                     "supporting_paragraph": f"Theo sổ văn bản của hệ thống, {period}."},
                              lists={"grid_1": [{"card_title": t, "card_caption": x} for t, x in cards]},
                              icons=["check-circle", "clock"]))
        if missing:
            plan += _table_slides("Đơn vị chưa gửi báo cáo", ["Mã đơn vị", "Đơn vị"],
                                  [[_nhan(m["ma_don_vi"]), _nhan(m["ten_don_vi"])] for m in missing],
                                  f"{so(len(missing))} đơn vị chưa có báo cáo {period} trong sổ văn bản.")

    plan.append(SlidePlan(layout=LAYOUT_CARDS, role="notes", note="Ghi chú dữ liệu.",
                          texts={"section_heading": "Ghi chú dữ liệu",
                                 "body_paragraph": "Các điểm cần lưu ý khi đọc và sử dụng số liệu."},
                          lists={"card_stack": [{"card_title": t, "card_description": x}
                                                for t, x in note_cards(data)]},
                          icons=["warning-circle", "info", "info", "info"]))
    if summary:
        items = summary["ket_luan"]
        plan.append(SlidePlan(layout=LAYOUT_GRID, role="conclusion",
                              note=" ".join(f"{x['tieu_de']}: {x['noi_dung']}" for x in items),
                              texts={"large_heading": "Kết luận",
                                     "supporting_paragraph": f"Ba điểm chính rút ra từ số liệu {period}."},
                              lists={"grid_1": [{"card_title": x["tieu_de"], "card_caption": x["noi_dung"]}
                                                for x in items]},
                              icons=["chart-bar", "lightbulb", "check-circle"]))
    return plan


def _du_bon(cards: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Layout thẻ dọc nhận đúng 4 thẻ."""
    filler = [("Nguồn", "CSDL ERP, truy vấn tổng hợp chỉ đọc."),
              ("Đối chiếu", "Số liệu trên slide đã được đối chiếu tự động với dữ liệu gốc.")]
    return (cards + filler)[:4] if len(cards) < 4 else cards[:4]


def code_numbers(plan: list[SlidePlan]) -> set[str]:
    """Mọi số do CODE viết ra trong kế hoạch (tỷ lệ, số trang, số thứ tự mục lục...).

    Đây là số thật - tính từ kết quả SQL - nên bước đối chiếu phải biết chúng. Chữ
    Qwen viết KHÔNG được tính vào đây: nó chỉ được dùng số của bộ dữ liệu.
    """
    known: set[str] = set()
    for p in plan:
        if p.role in ("conclusion",):
            continue
        texts = dict(p.texts)
        if p.role in ("title", "chart"):
            texts.pop("supporting_paragraph", None)
        collect_known_numbers(texts, known)
        collect_known_numbers(p.lists, known)
        if p.table:
            collect_known_numbers(p.table[0], known)
            collect_known_numbers(p.table[1], known)
        if p.dataset:
            collect_known_numbers(p.dataset.facts, known)
            collect_known_numbers(p.dataset.highlights, known)
            collect_known_numbers([v for _, vals in p.dataset.series for v in vals], known)
            collect_known_numbers(p.dataset.categories, known)
            collect_known_numbers([n for n, _ in p.dataset.series], known)
    return known


# --------------------------------------------------------------------------- #
# Điền layout
# --------------------------------------------------------------------------- #
def fit(text: str, limit: int) -> str:
    """Rút gọn theo ranh giới câu, rồi ranh giới từ - không để renderer cắt cứng giữa từ/số."""
    text = " ".join(str(text).split())
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "; "):
        i = cut.rfind(sep)
        if i > limit // 3:
            return cut[:i + 1].rstrip(";").rstrip() + ("" if sep == ". " else ".")
    return cut[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


@dataclass(slots=True)
class FillReport:
    written: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    icon_i: int = 0


def _set_text(e: dict[str, Any], text: str) -> str:
    text = fit(text, e.get("max_length") or 0)
    e["runs"] = [{**(e.get("runs") or [{}])[0], "text": text}]
    return text


def _fill(elems: list[dict[str, Any]], vals: dict[str, str], lists: dict[str, list[dict[str, str]]],
          icons: list[str], rep: FillReport) -> None:
    for e in elems:
        t, name = e.get("type"), e.get("name")
        if t == "text" and name and not e.get("decorative"):
            if name in vals:
                rep.written.append(_set_text(e, vals[name]))
            else:
                _set_text(e, "")
                rep.unmapped.append(name)
        elif t == "image":
            if e.get("is_icon"):
                if icons:
                    e["data"] = ICON.format(icons[rep.icon_i % len(icons)])
                rep.icon_i += 1
            else:
                e["data"] = PLACEHOLDER_IMAGE
        elif t in ("grid", "flex", "group") and name in lists:
            items = lists[name]
            lo, hi = e.get("min_children") or 1, e.get("max_children") or len(items)
            if not lo <= len(items) <= hi:
                raise DeckBuildError(f"{name}: {len(items)} phần tử ngoài giới hạn {lo}-{hi}")
            proto = e["children"]
            e["children"] = [copy.deepcopy(proto[i] if i < len(proto) else proto[0]) for i in range(len(items))]
            for child, item in zip(e["children"], items):
                _fill([child], {**vals, **item}, {}, icons, rep)
        else:
            if e.get("children"):
                _fill(e["children"], vals, lists, icons, rep)
            if e.get("child"):
                _fill([e["child"]], vals, lists, icons, rep)


def chart_payload(ds: Dataset) -> dict[str, Any]:
    return {"chart_type": ds.chart_type, "categories": list(ds.categories),
            "series": [{"name": n, "values": list(v)} for n, v in ds.series]}


def _fill_chart(ui: dict[str, Any], ds: Dataset) -> None:
    pay = chart_payload(ds)
    for c in ui["components"]:
        for el in c["elements"]:
            if el.get("type") == "chart":
                el.update(pay)
                el["legend"] = len(pay["series"]) > 1
                el["x_axis_title"] = ""
                el["y_axis_title"] = ""
                el["colors"] = PALETTE[:max(len(pay["series"]), 1)]
                el["data_labels"] = "top"


def _cell(proto: dict[str, Any], text: str) -> dict[str, Any]:
    cell = copy.deepcopy(proto)
    cell["runs"] = [{**(cell.get("runs") or [{}])[0], "text": text}]
    return cell


def _table_ui(base: dict[str, Any], columns: list[str], rows: list[list[str]]) -> dict[str, Any]:
    """Layout bảng toàn trang, dựng từ layout "chart + table" của template.

    Template chỉ có một layout bảng và nó dành nửa trang cho biểu đồ. Bảng chi tiết
    cần cả bề ngang, nên bỏ khối biểu đồ, kéo tiêu đề lên đầu trang và giãn bảng -
    vẫn đúng font, màu, kiểu ô của template.
    """
    ui = copy.deepcopy(base)
    comps = []
    for c in ui["components"]:
        if c["id"] == "left_visual_card":
            continue
        if c["id"] == "left_title_area":
            c["position"] = {"x": 64.0, "y": 40.0}
            for e in c["elements"]:
                e["position"] = {"x": 0.0, "y": 0.0}
                e["size"] = {"width": 1152.0, "height": 56.0}
                # Ô đã giãn hết bề ngang: giữ giới hạn 28 ký tự của ô gốc thì
                # "(2/6)" bị cắt và các trang của cùng một bảng trông như một.
                e["max_length"] = 70
        elif c["id"] == "right_copy_area":
            c["position"] = {"x": 64.0, "y": 100.0}
            for e in c["elements"]:
                e["size"] = {"width": 1152.0, "height": 40.0}
                e["max_length"] = 160
        elif c["id"] == "right_table_area":
            c["position"] = {"x": 64.0, "y": 150.0}
            for e in c["elements"]:
                if e.get("type") == "table":
                    head, body = e["columns"][0], e["rows"][0][0]
                    e["size"] = {"width": 1152.0, "height": 46.0 * (len(rows) + 1)}
                    e["columns"] = [_cell(head, col) for col in columns]
                    e["rows"] = [[_cell(body, val) for val in row] for row in rows]
                    e["max_columns"], e["max_rows"] = len(columns), max(len(rows), 1)
                    e["min_columns"], e["min_rows"] = 1, 1
        comps.append(c)
    ui["components"] = comps
    return ui


def build_slide(p: SlidePlan, index: int, presentation_id: str,
                template: dict[str, Any]) -> tuple[dict[str, Any], FillReport]:
    layouts = template["layouts"]
    if p.layout not in layouts:
        raise DeckBuildError(f"Template thiếu layout {p.layout}")
    ui = copy.deepcopy(layouts[p.layout])
    if p.table is not None:
        ui = _table_ui(ui, *p.table)
    rep = FillReport()
    for c in ui["components"]:
        _fill(c["elements"], p.texts, p.lists, p.icons, rep)
    if p.role == "chart" and p.dataset:
        _fill_chart(ui, p.dataset)
    if p.table is not None:
        rep.written += [*p.table[0], *(v for row in p.table[1] for v in row)]
    content: dict[str, Any] = {}
    for c in ui["components"]:
        _content(c["elements"], content.setdefault(c["id"], {}))
    content["__speaker_note__"] = p.note
    slide = {"id": str(uuid.uuid4()), "presentation": presentation_id, "layout_group": template["id"],
             "layout": p.layout, "index": index, "content": content, "speaker_note": p.note, "ui": ui}
    return slide, rep


def _content(elems: list[dict[str, Any]], node: dict[str, Any]) -> None:
    """`content` (dữ liệu màn hình sửa của Presenton) sinh từ `ui` đã điền - luôn khớp thứ được render."""
    for e in elems:
        t, name = e.get("type"), e.get("name")
        kids = e.get("children") or ([e["child"]] if e.get("child") else [])
        if t == "text" and name:
            node[name] = "".join(r.get("text", "") for r in e.get("runs") or [])
        elif t == "chart" and name:
            node[name] = {k: e[k] for k in ("chart_type", "categories", "series")}
        elif t == "table" and name:
            node[name] = {"columns": [_run_text(c) for c in e.get("columns") or []],
                          "rows": [[_run_text(c) for c in row] for row in e.get("rows") or []]}
        elif t == "image" and name:
            node[name] = ({"icon_query": "", "icon_url": e.get("data")} if e.get("is_icon")
                          else {"image_prompt": "", "image_url": e.get("data")})
        elif t in ("grid", "flex") and name and len(kids) > 1 and len({k.get("name") for k in kids}) == 1:
            node[name] = []
            for k in kids:
                item: dict[str, Any] = {}
                _content(k.get("children") or ([k["child"]] if k.get("child") else []), item)
                node[name].append(item)
        elif kids:
            _content(kids, node.setdefault(name, {}) if name else node)


def _run_text(cell: dict[str, Any]) -> str:
    return "".join(r.get("text", "") for r in cell.get("runs") or [])


# --------------------------------------------------------------------------- #
# Đối chiếu deck Presenton đã lưu
# --------------------------------------------------------------------------- #
def ui_texts(ui: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(e: dict[str, Any]) -> None:
        if e.get("type") == "text" and e.get("name") and not e.get("decorative"):
            out.append(_run_text(e))
        if e.get("type") == "table":
            out.extend(_run_text(c) for c in e.get("columns") or [])
            out.extend(_run_text(c) for row in e.get("rows") or [] for c in row)
        for k in e.get("children") or []:
            walk(k)
        if e.get("child"):
            walk(e["child"])

    for c in ui.get("components", []):
        for e in c.get("elements", []):
            walk(e)
    return out


def verify_deck(presentation: dict[str, Any], plan: list[SlidePlan],
                reports: list[FillReport]) -> dict[str, Any]:
    """Đọc lại deck Presenton đã lưu: đủ slide, đúng layout, chữ đúng nguyên văn,
    biểu đồ đúng số SQL, không chữ hệ lạ.

    Số lạ trong CHỮ thì `verify_node` soi trên file .pptx; ở đây kiểm thứ chỉ đọc
    được trên JSON của Presenton - biểu đồ, vì trong .pptx nó là ảnh.
    """
    slides = sorted(presentation.get("slides") or [], key=lambda s: s.get("index", 0))
    issues: list[dict[str, Any]] = []
    charts_ok = charts_total = 0
    if len(slides) != len(plan):
        issues.append({"type": "slide_count", "detail": f"{len(slides)} != {len(plan)}"})
    for s, p, rep in zip(slides, plan, reports):
        if s.get("layout") != p.layout:
            issues.append({"type": "layout", "slide": s.get("index"), "detail": f"{s.get('layout')} != {p.layout}"})
        written = set(rep.written)
        for text in ui_texts(s.get("ui") or {}):
            if text and text not in written:
                issues.append({"type": "foreign_text", "slide": s.get("index"), "detail": text[:80]})
            if (la := tim_chu_ngoai_he(text)):
                issues.append({"type": "foreign_script", "slide": s.get("index"), "detail": "".join(la)})
        if p.role == "chart" and p.dataset:
            charts_total += 1
            exp = chart_payload(p.dataset)
            els = [e for c in (s.get("ui") or {}).get("components", []) for e in c["elements"]
                   if e.get("type") == "chart"]
            ok = bool(els) and all(e.get("categories") == exp["categories"]
                                   and [x["values"] for x in e.get("series") or []]
                                   == [x["values"] for x in exp["series"]] for e in els)
            charts_ok += ok
            if not ok:
                issues.append({"type": "chart_mismatch", "slide": s.get("index"), "detail": p.dataset.key})
    return {"ok": not issues, "slides": len(slides), "planned": len(plan),
            "charts_ok": charts_ok, "charts_total": charts_total,
            "unmapped_slots": sum(len(r.unmapped) for r in reports), "issues": issues}
