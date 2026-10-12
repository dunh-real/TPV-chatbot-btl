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
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.documents.verify import collect_known_numbers, tim_chu_ngoai_he

# Đa sắc, tương phản đủ để phân biệt chuỗi số liệu (không chỉ các sắc xanh của theme).
PALETTE = ["#2563EB", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#0EA5E9", "#F97316", "#14B8A6"]
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

# Template momentum (Presenton): layout dùng cho từng loại slide - xem `chuyen_template`.
M_TITLE = "title_with_accent_footer_6891"
M_TOC = "center_title_agenda_grid_wave_7815"
M_STEPS = "title_process_cards_9539"            # 1-4 thẻ có icon, nối bằng mũi tên: phạm vi dữ liệu
M_CARDS = "title_over_cards_layout_8558"        # 2-6 thẻ đánh số: chỉ tiêu, kết luận
M_CHART = "title_chart_analysis_sections_9322"  # biểu đồ cột + 1-4 ý nổi bật bên cạnh
M_TABLE = "title_table_with_accents_1720"

# Khác biệt theo template: số dòng/cột một trang bảng, độ dài chữ Qwen viết (vừa ô của layout).
HO_SO: dict[str, dict[str, Any]] = {
    "modern": {},
    "momentum": {"table_rows": 8, "table_cols": 4, "summary": {"tong_quan": 180, "nhan_xet": 135}},
}


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
    return unicodedata.normalize("NFC", " ".join(str(text or "").split()))


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
    total_card: tuple[str, str] | None = None   # (giá trị, chú thích) cho slide chỉ tiêu - truy vấn động
    time_axis: bool = False     # danh mục là các kỳ (tháng, quý, năm): giữ thứ tự thời gian, cột đứng

    @property
    def chart_type(self) -> str:
        if self.time_axis:
            return "bar"
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
    if params.get("thang") and params.get("nam"):
        return f"tháng {params['thang']}/{params['nam']}"
    # Câu hỏi Text-to-SQL không nêu kỳ: không bịa tháng, nói đúng là theo câu hỏi.
    return "theo câu hỏi truy vấn"


def _heading(params: dict[str, Any]) -> str:
    if params.get("thang") and params.get("nam"):
        return f"Báo cáo T{params['thang']}/{params['nam']}"
    return "Báo cáo số liệu"


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


# --------------------------------------------------------------------------- #
# Số liệu từ truy vấn động (Text-to-SQL): bảng kết quả bất kỳ -> bộ dữ liệu slide
# --------------------------------------------------------------------------- #
# Không có tên chỉ tiêu cố định: cột nào là nhóm, cột nào là số đọc từ kiểu SQL của cột.
_KIEU_SO = {"int", "integer", "bigint", "smallint", "tinyint", "decimal", "numeric", "money",
            "smallmoney", "float", "real", "double", "number"}
# Cột tỷ lệ/trung bình/đơn giá: vẫn vẽ được, nhưng cộng lại thành "tổng" là sai nghĩa.
_KHONG_CONG = re.compile(r"t[yỷ]\s*l[eệ]|ph[aầ]n\s*tr[aă]m|%|trung\s*b[iì]nh|b[iì]nh\s*qu[aâ]n|"
                         r"[đd][oơ]n\s*gi[aá]|\bgi[aá]\b|avg|average|mean|median|rate|ratio|pct|percent|"
                         r"\bmin\b|\bmax\b|price", re.I)


@dataclass(slots=True)
class QueryParts:
    """Kết quả truy vấn động đã phân loại để dựng deck."""
    datasets: list[Dataset] = field(default_factory=list)           # có cột nhóm + cột số: biểu đồ
    kpis: list[tuple[str, str]] = field(default_factory=list)       # một dòng, chỉ có số: thẻ chỉ tiêu
    tables: list[tuple[str, list[str], list[list[str]]]] = field(default_factory=list)  # chỉ bảng
    notes: list[tuple[str, str]] = field(default_factory=list)      # cảnh báo, kết quả rỗng
    sources: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def _kieu(col: dict[str, Any]) -> str:
    return re.sub(r"\(.*$", "", str(col.get("data_type") or "")).strip().lower()


_COT_NHOM = re.compile(r"^(n[aă]m|th[aá]ng|qu[yý]|k[yỳ]|m[aã]|stt|year|month|quarter|period|code)\b", re.I)
_COT_KY = re.compile(r"^(n[aă]m|th[aá]ng|qu[yý]|k[yỳ]|year|month|quarter|period)\b", re.I)


def _la_cot_so(col: dict[str, Any], values: list[Any]) -> bool:
    name = str(col.get("name") or "")
    if name.lower() == "id" or name.endswith(("Id", "ID", "_id")):
        return False                                   # mã định danh không phải số liệu
    if _COT_NHOM.search(name) or _COT_NHOM.search(str(col.get("label") or "")):
        return False                                   # năm/tháng/mã là trục nhóm, không phải số liệu
    present = [v for v in values if v is not None]
    if _kieu(col) in _KIEU_SO:
        return all(_so_hoc(v) is not None for v in present)
    return bool(present) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present)


def _so_hoc(value: Any) -> int | float | None:
    """Giá trị số để vẽ/tính; Decimal đã được as_dict đổi sang chuỗi để không mất chính xác."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def _chu_so(value: Any) -> str:
    """Số nguyên viết kiểu Việt Nam; số thập phân giữ nguyên dạng gốc của truy vấn để đối chiếu khớp."""
    if value is None:
        return "—"
    n = _so_hoc(value)
    return so(n) if isinstance(n, int) else _nhan(value)


def _nhan_cot(col: dict[str, Any]) -> str:
    label = _nhan(col.get("label") or col.get("name"))
    return label + (f" ({col['unit']})" if col.get("unit") else "")


def _cong_duoc(col: dict[str, Any]) -> bool:
    return not _KHONG_CONG.search(f"{col.get('name', '')} {col.get('label', '')} {col.get('unit', '')}")


def _la_cot_ma(col: dict[str, Any]) -> bool:
    """Cột mã/định danh (mã phòng ban, mã tài sản): chỉ để trong bảng chi tiết, không làm nhãn nhóm."""
    name, label = str(col.get("name") or "").lower(), str(col.get("label") or "")
    return label.startswith(("Mã", "STT")) or name.startswith(("ma", "code")) or name.endswith(("code", "id"))


def _query_dataset(key: str, result: dict[str, Any], group_cols: list[int],
                   num_cols: list[int]) -> Dataset:
    cols, rows = result["columns"], result["rows"]
    main = num_cols[0]
    # Trục nhóm: cột tên có giá trị khác nhau ("Phòng ban", không ghép thêm "Mã phòng ban"); không có thì
    # cột mã; cột hằng (cùng một năm ở mọi dòng) xếp cuối vì không phân biệt được dòng nào.
    def hang(i: int) -> bool:
        return len(rows) > 1 and len({str(r[i]) for r in rows}) == 1
    cat = min(group_cols, key=lambda i: (hang(i), _la_cot_ma(cols[i]), group_cols.index(i)))
    group = _nhan(cols[cat].get("label") or cols[cat].get("name"))
    label = _nhan(cols[main].get("label") or cols[main].get("name"))
    unit = str(cols[main].get("unit") or "")
    don_vi = f" {unit}" if unit else ""

    def ten(row: list[Any]) -> str:
        return _nhan(row[cat]) if row[cat] is not None else "Chưa xác định"

    # Biểu đồ xếp theo cột số đầu; bảng giữ nguyên thứ tự của truy vấn. Trục là các kỳ (T1..T9) thì
    # biểu đồ cũng giữ thứ tự của truy vấn: xếp tháng theo giá trị là mất luôn xu hướng.
    ordered = sorted(rows, key=lambda r: -(_so_hoc(r[main]) or 0))
    time_axis = bool(_COT_KY.search(str(cols[cat].get("name") or ""))
                     or _COT_KY.search(str(cols[cat].get("label") or "")))
    cong = _cong_duoc(cols[main])
    series_cols = num_cols[:len(PALETTE) - 1]
    pairs = [(ten(r), [(_so_hoc(r[i]) or 0) for i in series_cols]) for r in (rows if time_axis else ordered)]
    others = 0
    if len(pairs) > CHART_MAX_BARS and not time_axis:   # kỳ không gộp: "Các tháng khác" vô nghĩa
        others = len(pairs) - (CHART_MAX_BARS - 1 if cong else CHART_MAX_BARS)
        if cong:      # số đếm/tổng: phần còn lại gộp thành một cột, vẫn đúng tổng
            rest = pairs[CHART_MAX_BARS - 1:]
            pairs = pairs[:CHART_MAX_BARS - 1] + [(f"{group} khác",
                                                   [sum(v[j] for _, v in rest) for j in range(len(series_cols))])]
        else:         # tỷ lệ/trung bình không cộng được: chỉ vẽ các mục cao nhất
            pairs = pairs[:CHART_MAX_BARS]
    series = [(_nhan(cols[i].get("label") or cols[i].get("name")), [v[j] for _, v in pairs])
              for j, i in enumerate(series_cols)]

    title = f"{label} theo {group.lower()}" if len(num_cols) == 1 else f"Số liệu theo {group.lower()}"
    ds = Dataset(key=key, title=title, short=label if len(num_cols) == 1 else "Số liệu",
                 group_label=group, unit=unit, categories=[n for n, _ in pairs], series=series,
                 table_columns=[_nhan_cot(c) for c in cols],
                 table_rows=[[_chu_so(v) if j in num_cols else (_nhan(v) if v is not None else "—")
                              for j, v in enumerate(r)] for r in rows],
                 grouped_others=others, time_axis=time_axis)

    values = [(ten(r), _so_hoc(r[main])) for r in ordered if _so_hoc(r[main]) is not None]
    total = sum(v for _, v in values) if cong else None
    facts: list[str] = []
    cards: list[tuple[str, str]] = []
    nhom = group.lower()
    if total is not None:
        # Cột đã là tổng ("Tổng", "Tổng số thiết bị"): không ghép thành "Tổng tổng".
        ten_tong = label if label.lower().startswith("tổng") else f"Tổng {label.lower()}"
        facts.append(f"{ten_tong} là {_chu_so(total)}{don_vi}, trên {len(rows)} {nhom}.")
        cards.append((ten_tong, f"{_chu_so(total)}{don_vi} trên {len(rows)} {nhom}."))
        ds.total_card = (f"{_chu_so(total)}{don_vi}", ten_tong)
    if values:
        top_name, top = values[0]
        share = f", chiếm {ty_le(top, total)}" if total else ""
        facts.append(f"{top_name} cao nhất với {_chu_so(top)}{don_vi}{share}.")
        cards.append(("Cao nhất", f"{top_name} với {_chu_so(top)}{don_vi}{share}."))
        if len(values) > 1:
            low_name, low = values[-1]
            facts.append(f"{low_name} thấp nhất với {_chu_so(low)}{don_vi}.")
            cards.append(("Thấp nhất", f"{low_name} với {_chu_so(low)}{don_vi}."))
        if (zero := [n for n, v in values if v == 0]):
            facts.append(f"Có {len(zero)}/{len(values)} {nhom} chưa có số liệu {label.lower()}.")
    if others:
        facts.append(f"Biểu đồ thể hiện {len(pairs)} cột, bảng chi tiết có đủ {len(rows)} {nhom}.")
    cards.append((f"Số {nhom}", f"{len(rows)} {nhom} có trong kết quả."))
    ds.facts, ds.highlights = facts, cards
    return ds


_MENH_LENH = re.compile(r"^(?:hãy\s+|vui lòng\s+|cho tôi\s+|giúp tôi\s+)*(?:tạo|lập|làm|soạn|xuất|dựng|vẽ|tổng hợp)"
                        r"(?:\s+cho tôi)?\s+(?:(?:một\s+)?(?:bộ\s+)?(?:slide|bản trình bày|báo cáo|file|văn bản|"
                        r"word|excel|pdf|powerpoint|pptx|docx)\s*)+(?:về\s+|cho\s+)?", re.I)


def noi_dung_cau_hoi(question: str) -> str:
    """"Tạo slide báo cáo số lượng nhân sự..." -> "Số lượng nhân sự...": câu lệnh của người dùng không
    phải nội dung báo cáo. Bỏ phần lịch sử hội thoại nối thêm sau dòng đầu."""
    first = _nhan(question.splitlines()[0] if question else "")
    text = _MENH_LENH.sub("", first).strip(" .:")
    return (text[:1].upper() + text[1:]) if text else first


def canh_bao_doc_duoc(warning: str) -> str:
    """Cảnh báo của Text-to-SQL viết cho kỹ thuật ("SQL đã kiểm tra quyền...") -> câu cho người đọc."""
    return re.sub(r"\bSQL\b", "Truy vấn", _nhan(warning))


def query_parts(data: dict[str, Any]) -> QueryParts:
    from app.documents.nhan_cot import lam_sach_ket_qua

    parts = QueryParts()
    for index, result in enumerate(data.get("queries") or []):
        lam_sach_ket_qua(result)      # nhãn cột luôn đọc được, chữ NFC - dù dữ liệu tới bằng đường nào
        cols, rows = result.get("columns") or [], result.get("rows") or []
        parts.sources += [s for s in result.get("source_tables") or [] if s not in parts.sources]
        question = noi_dung_cau_hoi(str(result.get("question") or ""))
        if question:
            parts.questions.append(question)
        parts.notes += [("Lưu ý phạm vi dữ liệu", canh_bao_doc_duoc(w)) for w in result.get("warnings") or []]
        if not rows:
            parts.notes.append(("Không có dữ liệu", "Không có số liệu thỏa điều kiện"
                                + (f" của nội dung \"{question[:80]}\"." if question else ".")))
            continue
        num_cols = [i for i, c in enumerate(cols) if _la_cot_so(c, [r[i] for r in rows])]
        group_cols = [i for i in range(len(cols)) if i not in num_cols]
        if num_cols and group_cols:
            parts.datasets.append(_query_dataset(f"bang_{index + 1}", result, group_cols, num_cols))
        elif num_cols and len(rows) == 1:
            parts.kpis += [((f"{_chu_so(rows[0][i])} {cols[i].get('unit') or ''}").strip(),
                            _nhan(cols[i].get("label") or cols[i].get("name"))) for i in num_cols]
        else:
            title = question[:60] or f"Kết quả tra cứu {index + 1}"
            parts.tables.append((title, [_nhan_cot(c) for c in cols],
                                 [[_chu_so(v) if i in num_cols else (_nhan(v) if v is not None else "—")
                                   for i, v in enumerate(r)] for r in rows]))
    return parts


def build_datasets(data: dict[str, Any], params: dict[str, Any]) -> list[Dataset]:
    if "queries" in data:
        return query_parts(data).datasets
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
    notes = sorted({n for k in ("personnel", "equipment")
                    if (n := ((data.get(k) or {}).get("scope") or {}).get("ghi_chu"))})
    compare = _compare(params)
    return [
        ("Đơn vị báo cáo", unit),
        ("Nguồn dữ liệu", "Hệ thống ERP của đơn vị, truy vấn chỉ đọc."),
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


def query_kpi_cards(parts: QueryParts, datasets: list[Dataset]) -> list[tuple[str, str]]:
    """Thẻ chỉ tiêu cho truy vấn động: kết quả một dòng + tổng của các bảng cộng được."""
    cards = list(parts.kpis) + [d.total_card for d in datasets if d.total_card]
    return list(dict.fromkeys(cards))[:6]


def query_scope_cards(parts: QueryParts, params: dict[str, Any], unit: str) -> list[tuple[str, str]]:
    # Không in tên bảng CSDL, không ghi "đối chiếu kỳ trước": truy vấn động chỉ lấy đúng kỳ được hỏi.
    so_bang = len(parts.sources)
    return [
        ("Đơn vị báo cáo", unit),
        ("Nguồn dữ liệu", "Hệ thống ERP của đơn vị, truy vấn chỉ đọc"
         + (f" trên {so_bang} bảng dữ liệu." if so_bang else ".")),
        ("Kỳ báo cáo", _period(params).capitalize() + "."),
        ("Nội dung báo cáo", (parts.questions[0][:150] if parts.questions else "Theo yêu cầu của người dùng.")),
    ]


def query_note_cards(parts: QueryParts) -> list[tuple[str, str]]:
    """Đúng 4 thẻ: cảnh báo của truy vấn và kết quả rỗng trước, thiếu thì ghi chú chung."""
    generic = [("Truy vấn tự động", "Số liệu do hệ thống tự truy vấn, chỉ đọc và chỉ trên dữ liệu được phép."),
               ("Kiểm chứng", "Mọi con số trên slide đã được đối chiếu tự động với kết quả truy vấn."),
               ("Thời điểm số liệu", "Dữ liệu nhập sau thời điểm lập báo cáo sẽ làm thay đổi kết quả."),
               ("Phạm vi kết quả", "Số liệu đúng theo điều kiện trong câu hỏi; đổi điều kiện sẽ cho kết quả khác.")]
    return (list(dict.fromkeys(parts.notes)) + generic)[:4]


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


def _trang(rows: list[list[str]], per_page: int = TABLE_ROWS_PER_SLIDE) -> list[list[list[str]]]:
    """Chia đều số dòng giữa các trang (8 dòng, 7/trang -> 4 + 4), không để trang cuối lẻ một dòng."""
    if not rows:
        return [[]]
    size = -(-len(rows) // -(-len(rows) // per_page))
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def _chia_cot(columns: list[str], rows: list[list[str]], max_cols: int) -> list[tuple[list[str], list[list[str]]]]:
    """Bảng trên slide không mang cột mã (mã phòng, STT) khi đã có cột tên làm khoá; layout chỉ nhận
    `max_cols` cột thì chia nhóm cột, giữ cột đầu làm khoá."""
    keep = [i for i, c in enumerate(columns) if i == 0 or not c.startswith(("Mã", "STT"))]
    if columns[:1] and not columns[0].startswith(("Mã", "STT")) and len(keep) < len(columns):
        columns, rows = [columns[i] for i in keep], [[r[i] for i in keep] for r in rows]
    if not max_cols or len(columns) <= max_cols:
        return [(columns, rows)]
    keep = list(range(len(columns)))
    if len(keep) <= max_cols:
        return [([columns[i] for i in keep], [[r[i] for i in keep] for r in rows])]
    rest = keep[1:]
    step = max_cols - 1
    return [([columns[0]] + [columns[i] for i in rest[k:k + step]],
             [[r[0]] + [r[i] for i in rest[k:k + step]] for r in rows]) for k in range(0, len(rest), step)]


def _table_slides(title: str, columns: list[str], rows: list[list[str]], intro: str,
                  per_page: int = TABLE_ROWS_PER_SLIDE, max_cols: int = 0) -> list[SlidePlan]:
    out = []
    for columns, rows in _chia_cot(columns, rows, max_cols):
        out += _table_pages(title, columns, rows, intro, per_page)
    return out


def _table_pages(title: str, columns: list[str], rows: list[list[str]], intro: str,
                 per_page: int) -> list[SlidePlan]:
    pages = _trang(rows, per_page)
    out = []
    for i, page in enumerate(pages, start=1):
        so_trang = f" ({i}/{len(pages)})" if len(pages) > 1 else ""
        out.append(SlidePlan(layout=LAYOUT_TABLE_BASE, role="table", note=f"{title}{so_trang}: {intro}",
                             texts={"main_heading": f"{title}{so_trang}", "intro_paragraph": intro},
                             table=(columns, page)))
    return out


def build_plan(data: dict[str, Any], params: dict[str, Any], unit: str,
               datasets: list[Dataset], summary: dict[str, Any] | None,
               report_date: str, template: str = "modern") -> list[SlidePlan]:
    """Toàn bộ nội dung deck. Không chữ nào trên slide nằm ngoài kế hoạch này.

    Kế hoạch dựng theo bộ ô của template `modern` (dạng chuẩn); template khác được chuyển sang
    layout của nó ở `chuyen_template` (gộp/đổi tên ô, rút gọn tiêu đề theo giới hạn layout)."""
    hs = HO_SO.get(template, {})
    per_page, max_cols = hs.get("table_rows", TABLE_ROWS_PER_SLIDE), hs.get("table_cols", 0)
    period = _period(params)
    notes = {n["bang"]: n["noi_dung"] for n in (summary or {}).get("nhan_xet", [])}
    parts = query_parts(data) if "queries" in data else None
    ten_bang = [d.title.lower() for d in datasets] + [t.lower() for t, _, _ in (parts.tables if parts else [])]
    overview = (summary or {}).get("tong_quan") or (
        f"Báo cáo số liệu {period} của {unit}: " + ("; ".join(ten_bang) or "kết quả truy vấn ERP") + ".")
    reporting = data.get("reporting") or {}
    kpis = query_kpi_cards(parts, datasets) if parts else kpi_cards(data)

    sections = [("Phạm vi và nguồn dữ liệu", "Đơn vị, nguồn, kỳ báo cáo, quy ước")]
    if len(kpis) >= 2:
        sections.append(("Chỉ tiêu chính", "Các con số tổng hợp của kỳ"))
    sections += [(d.short, d.title) for d in datasets]
    sections += [(t[:40], "Bảng kết quả truy vấn") for t, _, _ in (parts.tables if parts else [])]
    if reporting:
        sections.append(("Tình hình gửi báo cáo", "Đơn vị đã gửi và chưa gửi trong kỳ"))
    sections.append(("Ghi chú dữ liệu", "Điểm cần lưu ý khi đọc số liệu"))
    if summary:
        sections.append(("Kết luận", "Ba điểm chính rút ra từ số liệu"))

    plan: list[SlidePlan] = [
        SlidePlan(layout=LAYOUT_TITLE, role="title", note=overview, texts={
            "primary_heading": _heading(params),
            "supporting_paragraph": overview,
            "badge_initials": "".join(w[0] for w in unit.split()[:2]).upper() or "BC",
            "card_primary_text": unit,
            "card_secondary_text": f"Nguồn ERP, lập ngày {report_date}"}),
        SlidePlan(layout=LAYOUT_TOC, role="toc", note="Nội dung trình bày.",
                  texts={"header_text": "Nội dung trình bày"},
                  lists={"entry_grid": [{"badge_number": str(i), "entry_title": t, "entry_description": d}
                                        for i, (t, d) in enumerate(sections, start=1)]}),
        SlidePlan(layout=LAYOUT_CARDS, role="scope", note="Phạm vi và nguồn dữ liệu.",
                  texts={"section_heading": "Phạm vi và nguồn dữ liệu",
                         "body_paragraph": f"Bộ slide tổng hợp số liệu {period} trực tiếp từ CSDL ERP."},
                  lists={"card_stack": [{"card_title": t, "card_description": d}
                                        for t, d in (query_scope_cards(parts, params, unit) if parts
                                                     else scope_cards(data, params, unit))]},
                  icons=["buildings", "database", "calendar", "ruler"]),
    ]
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
                              texts={"section_heading": f"Điểm nổi bật về {d.short.lower()}",
                                     "body_paragraph": f"Các điểm chính về {d.title.lower()} {period}."},
                              lists={"card_stack": [{"card_title": t, "card_description": x}
                                                    for t, x in _du_bon(d.highlights)]},
                              icons=["sigma", "arrow-up", "arrows-left-right", "warning-circle"]))
        intro = f"Số liệu chi tiết về {d.title.lower()} {period}, lấy từ hệ thống ERP."
        plan += _table_slides(f"Chi tiết {d.short.lower()}", d.table_columns, d.table_rows, intro,
                              per_page, max_cols)

    for title, columns, rows in (parts.tables if parts else []):
        plan += _table_slides(title[:40], columns, rows,
                              f"Số liệu chi tiết {period}, lấy từ hệ thống ERP.", per_page, max_cols)

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
                                  f"{so(len(missing))} đơn vị chưa có báo cáo {period} trong sổ văn bản.",
                                  per_page, max_cols)

    plan.append(SlidePlan(layout=LAYOUT_CARDS, role="notes", note="Ghi chú dữ liệu.",
                          texts={"section_heading": "Ghi chú dữ liệu",
                                 "body_paragraph": "Các điểm cần lưu ý khi đọc và sử dụng số liệu."},
                          lists={"card_stack": [{"card_title": t, "card_description": x}
                                                for t, x in (query_note_cards(parts) if parts
                                                             else note_cards(data))]},
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
    filler = [("Nguồn", "Hệ thống ERP của đơn vị, truy vấn tổng hợp chỉ đọc."),
              ("Đối chiếu", "Số liệu trên slide đã được đối chiếu tự động với dữ liệu gốc.")]
    return (cards + filler)[:4] if len(cards) < 4 else cards[:4]


def code_numbers(plan: list[SlidePlan]) -> set[str]:
    """Mọi số do CODE viết ra trong kế hoạch (tỷ lệ, số trang, số thứ tự mục lục...).

    Đây là số thật - tính từ kết quả SQL - nên bước đối chiếu phải biết chúng. Chữ
    Qwen viết KHÔNG được tính vào đây: nó chỉ được dùng số của bộ dữ liệu.
    """
    known: set[str] = set()
    for i in range(1, len(plan) + 1):           # số trang ở chân slide
        known |= {str(i), f"{i:02d}"}
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
    """Rút gọn theo ranh giới câu, rồi vế câu, rồi từ - không cắt giữa từ/số, không thêm "…"."""
    lines = [" ".join(line.split()) for line in str(text).split("\n")]
    text = "\n".join(line for line in lines if line)
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "; ", ", "):
        i = cut.rfind(sep)
        if i > limit // 3:
            return cut[:i].rstrip(" ,;:-") + "."
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:-")


# Ô tiêu đề/nhãn (không chấm câu) và ô đoạn văn (luôn kết thúc bằng dấu câu).
_O_TIEU_DE = re.compile(r"(heading|title|label|badge|marker|pagination|folio|page|tag|price|value|initials|"
                        r"header|headline|metadata|primary_text|secondary_text|number)", re.I)
_DAU_KET = (".", "!", "?", ":", "%", ")")


_O_CAU = re.compile(r"(subtitle|paragraph|description|caption|summary|body|intro)", re.I)


def kieu_o(name: str) -> str:
    """Mục lục là nhãn ngắn (không chấm); ô phụ đề/mô tả là câu dù tên ô có chữ "title"."""
    name = name or ""
    if "agenda" in name.lower():
        return "tieu_de"
    if _O_CAU.search(name):
        return "cau"
    return "tieu_de" if _O_TIEU_DE.search(name) else "cau"


def chuan_hoa(text: str, kieu: str = "cau") -> str:
    """Chuẩn hoá trình bày: khoảng trắng, dấu câu, viết hoa chữ đầu; bỏ ký hiệu kỹ thuật ("·", "…")."""
    out = []
    for line in str(text or "").split("\n"):
        t = " ".join(line.split())
        t = t.replace(" · ", ", ").replace("·", ",").replace("…", "").replace("...", "")
        t = re.sub(r"\s+([,.;:!?%)])", r"\1", t)                  # không cách trước dấu câu
        t = re.sub(r"([,;:])(?=[^\s\d/])", r"\1 ", t)               # cách sau dấu phẩy/chấm phẩy
        t = re.sub(r"([.,;:])[.,;]+", r"\1", t)                     # dấu câu lặp
        t = re.sub(r"\(\s+", "(", t).strip(" ,;")
        if t:
            t = t[:1].upper() + t[1:]
        out.append(t)
    t = "\n".join(x for x in out if x)
    if not t:
        return ""
    if kieu == "tieu_de":
        return t.rstrip(" .;:,")
    return t if t.endswith(_DAU_KET) else t.rstrip(" ,;") + "."


@dataclass(slots=True)
class FillReport:
    written: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    icon_i: int = 0
    page: int = 0
    chart_image: str = ""       # đường dẫn ảnh biểu đồ đã đặt vào slide (Presenton)


_O_TRANG = re.compile(r"(marker|pagination|page_number|folio|page_label|footer_index)", re.I)


def _set_text(e: dict[str, Any], text: str) -> str:
    kieu = kieu_o(e.get("name") or "")
    limit = e.get("max_length") or 0
    text = chuan_hoa(text, kieu)
    if limit and len(text) > limit:
        text = chuan_hoa(fit(text, limit - (1 if kieu == "cau" else 0)), kieu)
    e["runs"] = [{**(e.get("runs") or [{}])[0], "text": text}]
    return text


def _fill(elems: list[dict[str, Any]], vals: dict[str, str], lists: dict[str, list[dict[str, str]]],
          icons: list[str], rep: FillReport) -> None:
    for e in elems:
        t, name = e.get("type"), e.get("name")
        if t == "text" and name and not e.get("decorative"):
            if name in vals:
                rep.written.append(_set_text(e, vals[name]))
            elif rep.page and _O_TRANG.search(name):
                e["runs"] = [{**(e.get("runs") or [{}])[0], "text": f"{rep.page:02d}"}]
                rep.written.append(f"{rep.page:02d}")
            else:
                _set_text(e, "")
                rep.unmapped.append(name)
        elif t == "image":
            if e.get("is_icon"):
                if icons:
                    e["data"] = ICON.format(icons[rep.icon_i % len(icons)])
                rep.icon_i += 1
            elif not (e.get("decorative") and e.get("data")):
                # Ảnh trang trí của template (sóng, góc, khung) giữ nguyên; ô ảnh nội dung dùng ảnh giữ chỗ.
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


# Ô biểu đồ khi layout không khai kích thước (px của slide 1280x720).
DEFAULT_CHART_SLOT = (560.0, 334.0)


def chart_slot(template: dict[str, Any], layout: str) -> tuple[float, float]:
    """Kích thước ô biểu đồ của layout: ảnh biểu đồ vẽ đúng tỷ lệ ô này."""
    for c in (template["layouts"].get(layout) or {}).get("components", []):
        for el in c["elements"]:
            if el.get("type") == "chart" and (size := el.get("size")):
                return float(size["width"]), float(size["height"])
    return DEFAULT_CHART_SLOT


def _fill_chart(ui: dict[str, Any], image_url: str) -> bool:
    """Ô biểu đồ của layout -> ô ảnh mang biểu đồ PNG do code vẽ (`app.documents.charts`).

    Không để renderer của Presenton vẽ: nó ẩn bớt nhãn khi nhiều danh mục, màu theo theme,
    và .pptx xuất ra vẫn là ảnh chụp - còn đường tự dựng thì ra một hình khác hẳn. Giữ nguyên
    vị trí, kích thước và tên ô nên bố cục slide không đổi.
    """
    replaced = False
    for c in ui["components"]:
        for i, el in enumerate(c["elements"]):
            if el.get("type") == "chart":
                c["elements"][i] = {"type": "image", "position": el["position"], "size": el["size"],
                                    "data": image_url, "fit": "contain", "decorative": False,
                                    "name": el.get("name") or "chart_image", "is_icon": False}
                replaced = True
    return replaced


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


def _table_ui_momentum(base: dict[str, Any], columns: list[str], rows: list[list[str]]) -> dict[str, Any]:
    """Layout bảng của momentum chỉ chứa 4 dòng chữ cỡ 23: kéo bảng lên sát đoạn giới thiệu và giảm
    cỡ chữ để chứa tối đa 8 dòng mà không đè lên chân trang (y < 650). Chữ tiêu đề cột của template
    màu trắng trên nền xanh nhạt 8% (gần như không đọc được) -> đổi sang màu nhấn của template."""
    ui = copy.deepcopy(base)
    for c in ui["components"]:
        if c["id"] != "comparison_table":
            continue
        c["position"] = {**c.get("position", {}), "y": 285.0}
        for e in c["elements"]:
            if e.get("type") != "table":
                continue
            head, body = copy.deepcopy(e["columns"][0]), copy.deepcopy(e["rows"][0][0])
            for cell in (head, body):
                for holder in [cell, *(cell.get("runs") or [])]:
                    if isinstance(holder.get("font"), dict):
                        holder["font"]["size"] = 16.0
                        if cell is head:
                            holder["font"]["color"] = "#1A3DB3"
            e["size"] = {"width": e["size"]["width"], "height": min(42.0, 365.0 / (len(rows) + 1)) * (len(rows) + 1)}
            e["columns"] = [_cell(head, col) for col in columns]
            e["rows"] = [[_cell(body, val) for val in row] for row in rows]
            e["max_columns"], e["max_rows"] = len(columns), max(len(rows), 1)
            e["min_columns"], e["min_rows"] = 1, 1
    return ui


_M_NOI_DUNG = {M_STEPS, M_CARDS, M_CHART, M_TABLE}


def _thu_tieu_de(ui: dict[str, Any], size: float = 68.0) -> None:
    """Tiêu đề momentum cỡ 82-89, giãn dòng 0.9: chữ có phần dưới dòng (p, g, y) đè lên đoạn giới thiệu
    đặt ngay dưới (cách ~80px). Cỡ 68 giữ được độ đậm của template mà không chạm đoạn dưới."""
    def walk(e: dict[str, Any]) -> None:
        if e.get("type") == "text" and (e.get("font") or {}).get("size", 0) >= 80:
            for holder in [e, *(e.get("runs") or [])]:
                if isinstance(holder.get("font"), dict):
                    holder["font"]["size"] = size
        for k in e.get("children") or []:
            walk(k)
        if e.get("child"):
            walk(e["child"])
    for c in ui["components"]:
        for e in c["elements"]:
            walk(e)


def build_slide(p: SlidePlan, index: int, presentation_id: str, template: dict[str, Any],
                chart_images: dict[str, str] | None = None) -> tuple[dict[str, Any], FillReport]:
    """`chart_images`: khoá bộ dữ liệu -> đường dẫn ảnh biểu đồ đã tải lên Presenton."""
    layouts = template["layouts"]
    if p.layout not in layouts:
        raise DeckBuildError(f"Template thiếu layout {p.layout}")
    ui = copy.deepcopy(layouts[p.layout])
    if p.table is not None:
        ui = (_table_ui_momentum if p.layout == M_TABLE else _table_ui)(ui, *p.table)
    if p.layout in _M_NOI_DUNG:
        _thu_tieu_de(ui)
    rep = FillReport(page=index + 1)
    for c in ui["components"]:
        _fill(c["elements"], p.texts, p.lists, p.icons, rep)
    if p.role == "chart" and p.dataset:
        url = (chart_images or {}).get(p.dataset.key)
        if not url:
            raise DeckBuildError(f"Chưa có ảnh biểu đồ cho bộ dữ liệu {p.dataset.key}")
        if not _fill_chart(ui, url):
            raise DeckBuildError(f"Layout {p.layout} không có ô biểu đồ")
        rep.chart_image = url
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


# --------------------------------------------------------------------------- #
# Chuyển kế hoạch chuẩn sang layout của template khác
# --------------------------------------------------------------------------- #
_NGAN = {"Phạm vi và nguồn dữ liệu": "Phạm vi dữ liệu", "Tình hình gửi báo cáo": "Gửi báo cáo"}


def _ngan(text: str, limit: int) -> str:
    """Tiêu đề ngắn cho ô hẹp: bản rút gọn có sẵn, không có thì cắt ở ranh giới từ (không chấm, không "…")."""
    text = _NGAN.get(text, text)
    return text if len(text) <= limit else text[:limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")


def _the(p: SlidePlan, list_name: str) -> list[dict[str, str]]:
    return p.lists.get(list_name) or []


def chuyen_template(plan: list[SlidePlan], template: str) -> list[SlidePlan]:
    """Kế hoạch dựng theo ô của `modern` -> layout của `template`. Template chưa có ánh xạ: giữ nguyên.

    momentum: slide "điểm nổi bật" gộp vào cạnh biểu đồ (layout biểu đồ có sẵn cột ý chính), bớt
    một slide thẻ lặp lại cho mỗi bảng số liệu."""
    if template != "momentum":
        return plan
    out: list[SlidePlan] = []
    for i, p in enumerate(plan):
        t = p.texts
        if p.role == "title":
            m = re.match(r"^Báo cáo T(\d+)/(\d+)$", t["primary_heading"])
            cover = f"Báo cáo\n{m.group(1)}/{m.group(2)}" if m else "Báo cáo\nsố liệu"
            out.append(SlidePlan(layout=M_TITLE, role="title", note=p.note, texts={
                "cover_title": cover, "cover_subtitle": t["supporting_paragraph"],
                "presenter_metadata": f"Đơn vị: {t['card_primary_text']}",
                "date_metadata": t["card_secondary_text"].replace("Nguồn ERP, l", "L")}))
        elif p.role == "toc":
            out.append(SlidePlan(layout=M_TOC, role="toc", note=p.note, texts={"slide_title": t["header_text"]},
                                 lists={"grid_items_elements_with_descrtipiton_title": [
                                     {"agenda_item_label": f"{int(x['badge_number']):02d}",
                                      "agenda_item_description": _ngan(x["entry_title"], 18)}
                                     for x in _the(p, "entry_grid")[:8]]}))
        elif p.role == "scope":
            out.append(SlidePlan(layout=M_STEPS, role="scope", note=p.note, icons=p.icons,
                                 texts={"main_title": t["section_heading"], "intro_description": t["body_paragraph"]},
                                 lists={"flex_elements": [{"step_heading": x["card_title"],
                                                           "step_description": x["card_description"]}
                                                          for x in _the(p, "card_stack")[:4]]}))
        elif p.role == "highlights":
            continue                                     # đã nằm cạnh biểu đồ
        elif p.role == "chart":
            nxt = plan[i + 1] if i + 1 < len(plan) else None
            y = _the(nxt, "card_stack") if nxt and nxt.role == "highlights" and nxt.dataset is p.dataset else []
            items = [{"note_label": f"{k:02d}", "note_description": f"{x['card_title']}: {x['card_description']}"}
                     for k, x in enumerate(y[:4], start=1)]
            out.append(SlidePlan(layout=M_CHART, role="chart", note=p.note, dataset=p.dataset,
                                 texts={"main_title": t["primary_heading"], "intro_description": t["supporting_paragraph"],
                                        "section_heading": "Điểm nổi bật"},
                                 lists={"section_grid_lists": items} if items else {}))
        elif p.role == "table":
            heading = t["main_heading"]
            trang = m.group(0) if (m := re.search(r"\(\d+/\d+\)$", heading)) else ""
            intro = t["intro_paragraph"] if heading.startswith("Chi tiết") else f"{heading}: {t['intro_paragraph']}"
            intro = intro.rstrip(".") + (f", trang {trang.strip('()')}." if trang and heading.startswith("Chi tiết") else ".")
            out.append(SlidePlan(layout=M_TABLE, role="table", note=p.note, table=p.table,
                                 texts={"slide_title": "Chi tiết", "intro_paragraph": intro}))
        elif p.role == "notes":
            # Layout timeline của momentum dựng sẵn đúng 4 thẻ (thẻ đầu, lưới 2 thẻ, thẻ mũi tên): ít ghi chú
            # hơn thì thẻ bị lặp/tràn mép -> dùng layout thẻ đánh số như kết luận.
            out.append(SlidePlan(layout=M_CARDS, role="notes", note=p.note, icons=p.icons,
                                 texts={"main_heading": _ngan(t["section_heading"], 17),
                                        "subtitle_text": t["body_paragraph"]},
                                 lists={"cards_row": [{"card_badge_label": f"{k:02d}", "card_title": x["card_title"],
                                                       "card_description": x["card_description"]}
                                                      for k, x in enumerate(_the(p, "card_stack")[:3], start=1)]}))
        else:                                            # kpi, reporting, conclusion: thẻ đánh số
            out.append(SlidePlan(layout=M_CARDS, role=p.role, note=p.note, icons=p.icons,
                                 texts={"main_heading": _ngan(t["large_heading"], 17),
                                        "subtitle_text": t["supporting_paragraph"]},
                                 lists={"cards_row": [{"card_badge_label": f"{k:02d}", "card_title": x["card_title"],
                                                       "card_description": x["card_caption"]}
                                                      for k, x in enumerate(_the(p, "grid_1")[:6], start=1)]}))
    return out


def theme_cho(template: str, theme: dict[str, Any]) -> dict[str, Any]:
    """Theme gửi Presenton: giữ font/màu nền của template, thay dải màu biểu đồ bằng bảng đa sắc
    (theme momentum gốc chỉ có các sắc xanh nên biểu đồ nhiều chuỗi trông như một màu)."""
    out = copy.deepcopy(theme)
    colors = out.setdefault("colors", {})
    for i, color in enumerate(PALETTE):
        colors[f"graph_{i}"] = color
    return out


def verify_deck(presentation: dict[str, Any], plan: list[SlidePlan],
                reports: list[FillReport]) -> dict[str, Any]:
    """Đọc lại deck Presenton đã lưu: đủ slide, đúng layout, chữ đúng nguyên văn,
    đúng ảnh biểu đồ đã vẽ, không chữ hệ lạ.

    Số lạ trong CHỮ thì `verify_node` soi trên file .pptx. Số trên biểu đồ do code vẽ
    thẳng từ bộ dữ liệu; ở đây chỉ cần chắc slide mang đúng ảnh đó và Presenton không
    còn ô biểu đồ nào tự vẽ.
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
            els = [e for c in (s.get("ui") or {}).get("components", []) for e in c["elements"]]
            ok = (bool(rep.chart_image) and not any(e.get("type") == "chart" for e in els)
                  and any(e.get("type") == "image" and e.get("data") == rep.chart_image for e in els))
            charts_ok += ok
            if not ok:
                issues.append({"type": "chart_mismatch", "slide": s.get("index"), "detail": p.dataset.key})
    from app.documents.nhan_cot import la_ten_truong

    # Tên trường CSDL lọt lên slide: cảnh báo kèm vị trí (nhãn cột đã được đặt trước khi dựng deck).
    field_like = [{"slide": r_i + 1, "text": w[:80]} for r_i, r in enumerate(reports) for w in r.written
                  if la_ten_truong(w)]
    return {"ok": not issues, "slides": len(slides), "planned": len(plan), "field_like": field_like,
            "charts_ok": charts_ok, "charts_total": charts_total,
            "unmapped_slots": sum(len(r.unmapped) for r in reports), "issues": issues}
