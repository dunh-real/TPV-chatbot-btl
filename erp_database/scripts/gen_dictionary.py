"""Sinh DATA-DICTIONARY.md (từ điển dữ liệu đủ 180 bảng) từ profile.json.

    python scripts/gen_dictionary.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = [  # (tiền tố, tên module, mô tả)
    ("Abp", "ABP Framework - nền tảng", "Người dùng, tenant, phân quyền, cài đặt, audit, thông báo (khung ASP.NET Zero / ABP)"),
    ("App", "App - tiện ích ứng dụng", "Quy trình phê duyệt, chat, bạn bè, file nhị phân, gói thuê bao"),
    ("Dms", "Dms - danh mục & tổ chức", "Nhân viên, phòng ban, chức danh, địa giới, đội xe"),
    ("Hrm", "Hrm - nhân sự", "Hồ sơ, chấm công, bảng công, nghỉ phép, công tác, hợp đồng, khen thưởng/kỷ luật"),
    ("Att", "Att - lương & KPI", "Bảng lương, phiếu lương, thành phần lương, ca làm, tăng ca, KPI"),
    ("Fin", "Fin - tài chính", "Đề nghị thanh toán, phiếu chi/thu, tạm ứng, ví, tài khoản ngân hàng, dự án"),
    ("Asm", "Asm - tài sản", "Tài sản, loại tài sản, cấp phát tài sản"),
    ("Meeting", "Meeting - cuộc họp", "Cuộc họp, lịch sử, transcript, tóm tắt"),
    ("AI", "AI - trợ lý AI", "Hội thoại chatbot, tóm tắt báo cáo, slide, mind map, mẫu báo cáo"),
    ("Documents", "Documents - tài liệu chatbot", "Tài liệu người dùng tải lên cho chatbot"),
    ("Rcm", "Rcm - tuyển dụng", "Kế hoạch tuyển dụng"),
    ("SIPM", "SIPM", "Chấm công tăng ca (chưa có dữ liệu)"),
    ("HangFire", "HangFire - job nền", "Hàng đợi job nền của Hangfire"),
    ("", "Khác", "Migration EF, token thiết bị, sơ đồ SSMS"),
]


def fmt(n) -> str:
    return f"{n or 0:,}".replace(",", ".")


def module_of(t: dict) -> str:
    if t["sch"] == "HangFire":
        return "HangFire"
    name = t["tbl"]
    for prefix, *_ in MODULES:
        if prefix and prefix != "HangFire" and (name.startswith(prefix + "_") or (prefix in ("Abp", "App") and name.startswith(prefix))):
            return prefix
    return ""


def type_str(c: dict) -> str:
    t, ml = c["type"], c["max_length"]
    if t in ("nvarchar", "nchar"):
        return f"{t}({'max' if ml == -1 else ml // 2})"
    if t in ("varchar", "char", "varbinary", "binary"):
        return f"{t}({'max' if ml == -1 else ml})"
    if t in ("decimal", "numeric"):
        return f"{t}({c['precision']},{c['scale']})"
    return t


def main() -> None:
    p = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))
    groups: dict[str, list] = {m[0]: [] for m in MODULES}
    for t in p["tables"]:
        groups[module_of(t)].append(t)
    L = ["# Từ điển dữ liệu - `bqp-private-ai-assistant`", "",
         f"Sinh tự động từ `profile.json` (server {p['server']['server']}, SQL Server {p['server']['version']}). "
         "Số dòng là số dòng vật lý, **gồm cả dòng đã xoá mềm** (`IsDeleted = 1`). Giá trị phân loại chỉ liệt kê "
         "cho cột ít giá trị (≤ 25), không lấy cột có thể chứa thông tin cá nhân.", "",
         "Ký hiệu cột: `PK` khoá chính, `FK→` khoá ngoại, `?` cho phép NULL, `ID` identity.", "", "## Mục lục", ""]
    for prefix, title, desc in MODULES:
        ts = groups[prefix]
        if ts:
            L.append(f"- [{title}](#{title.lower().replace(' ', '-').replace('&', '').replace('--', '-')}) - "
                     f"{len(ts)} bảng, {fmt(sum(t['rows'] for t in ts))} dòng")
    for prefix, title, desc in MODULES:
        ts = sorted(groups[prefix], key=lambda t: -t["rows"])
        if not ts:
            continue
        L += ["", f"## {title}", "", desc, "", "| Bảng | Dòng | Đã xoá mềm | Cột | Dung lượng | Khoá ngoại ra | Được tham chiếu bởi |",
              "|---|---|---|---|---|---|---|"]
        for t in ts:
            sd = t["profile"].get("soft_deleted")
            sd_s = f"{fmt(sd['deleted'])} ({sd['deleted'] * 100 // sd['total']}%)" if sd and sd["total"] else "-"
            outs = ", ".join(sorted({f["to_table"].split(".")[-1] for f in t["fk_out"]})) or "-"
            ins = len({f["from_table"] for f in t["fk_in"]})
            L.append(f"| [{t['sch']}.{t['tbl']}](#{(t['sch'] + t['tbl']).lower().replace('_', '')}) | {fmt(t['rows'])} | {sd_s} | "
                     f"{len(t['columns'])} | {fmt(t['size_kb'])} KB | {outs} | {ins or '-'} |")
        for t in ts:
            pr = t["profile"]
            L += ["", f"### {t['sch']}.{t['tbl']}", f'<a id="{(t["sch"] + t["tbl"]).lower().replace("_", "")}"></a>', ""]
            meta = [f"**{fmt(t['rows'])} dòng**", f"PK `{t['pk'] or 'không có'}`"]
            if "soft_deleted" in pr and pr["soft_deleted"]["total"]:
                meta.append(f"xoá mềm {fmt(pr['soft_deleted']['deleted'])}/{fmt(pr['soft_deleted']['total'])}")
            if "time_range" in pr:
                tr = pr["time_range"]
                meta.append(f"`{tr['column']}` {str(tr['min'])[:10]} → {str(tr['max'])[:10]}")
            if "by_tenant" in pr:
                meta.append("tenant: " + ", ".join(f"{r['TenantId'] if r['TenantId'] is not None else 'host'}={fmt(r['n'])}"
                                                   for r in pr["by_tenant"][:8]))
            L.append(" · ".join(meta))
            fk_by_col = {f["from_col"]: f"FK→ {f['to_table']}.{f['to_col']}" for f in t["fk_out"]}
            pk_cols = set((t["pk"] or "").split(","))
            L += ["", "| # | Cột | Kiểu | Ràng buộc | NULL (số dòng) | Giá trị phân loại |", "|---|---|---|---|---|---|"]
            for c in t["columns"]:
                flags = []
                if c["name"] in pk_cols:
                    flags.append("PK")
                if c["is_identity"]:
                    flags.append("ID")
                if c["name"] in fk_by_col:
                    flags.append(fk_by_col[c["name"]])
                if c["default_def"]:
                    flags.append(f"default {c['default_def']}")
                nul = pr.get("nulls", {}).get(c["name"])
                cat = pr.get("categories", {}).get(c["name"])
                cat_s = ", ".join(f"`{v['v']}`={fmt(v['n'])}" for v in cat[:10]) if cat else ""
                L.append(f"| {c['column_id']} | `{c['name']}` | {type_str(c)}{'?' if c['is_nullable'] else ''} | "
                         f"{'; '.join(flags)} | {fmt(nul) if nul else ''} | {cat_s} |")
            if t["indexes"]:
                L += ["", "Index: " + "; ".join(f"`{i['name']}` ({i['cols']}{', unique' if i['is_unique'] else ''}"
                                                 f"{', lọc ' + i['filter_definition'] if i['filter_definition'] else ''})"
                                                 for i in t["indexes"])]
            if t["fk_in"]:
                L += ["", "Được tham chiếu bởi: " + ", ".join(sorted({f"{f['from_table']}.{f['from_col']}" for f in t["fk_in"]}))]
    (ROOT / "DATA-DICTIONARY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("DATA-DICTIONARY.md", len(L), "dòng")


if __name__ == "__main__":
    main()
