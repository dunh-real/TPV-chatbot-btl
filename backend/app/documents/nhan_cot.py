"""Nhãn tiếng Việt cho cột kết quả truy vấn ERP, để Word/slide không in tên trường CSDL.

Text-to-SQL trả cột với tên alias do model đặt (`soluongnhansu`, `cuoi_thang_8`, `employee_count`)
và thường không kèm nhãn. Tên đó lọt thẳng lên tài liệu thì người đọc thấy cú pháp CSDL thay vì
"Số nhân sự (người)". Thứ tự lấy nhãn:

  1. nhãn có sẵn trong kết quả, nếu đã là chữ tiếng Việt đọc được;
  2. Qwen đặt nhãn + đơn vị cho các cột còn thiếu, dựa trên câu hỏi và giá trị mẫu - đầu ra bị kiểm
     (có dấu, không dạng tên trường, không trùng nhau), sai thì hỏi lại kèm lý do;
  3. nhãn trung tính "Cột n" - không bao giờ trả lại tên trường thô.

KHÔNG dùng từ điển tách âm tiết ("soluong" -> "Số lượng"): từ điển chỉ phủ được tên đã gặp, đoán
sai dấu khi âm tiết mơ hồ ("tinh" là "tính" hay "tình", "chung" là "chung" hay "chủng"), và không
hiểu được nghĩa cột ("cuoi_thang_8" là "Nhân sự cuối tháng 8" chứ không phải "Cuối tháng 8"). Qwen
thấy câu hỏi và giá trị mẫu nên đặt được nhãn đúng nghĩa.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

_DAU_VIET = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
# Chữ trông như tên trường CSDL: có gạch dưới, camelCase, hoặc một từ ASCII viết liền dài.
_TEN_TRUONG = re.compile(r"\b(?:[A-Za-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+|[a-z]{9,})\b")
# Đơn vị viết bằng ký tự ASCII mà vẫn đúng (không phải chữ Việt bị mất dấu như "nguoi").
_DON_VI_ASCII = {"kg", "km", "m", "m2", "m3", "usd", "vnd", "%", "h", "gb", "tb", "mb"}

MAX_LAN_HOI = 2
# Bối cảnh nghiệp vụ để Qwen hiểu tên cột tiếng Anh của ERP - không phải bảng ánh xạ: nhãn vẫn do Qwen đặt.
THUAT_NGU = ("Thuật ngữ của hệ thống ERP này: tenant/thuê bao = đơn vị thuê bao; department = phòng ban; "
             "position = chức vụ; asset = thiết bị/tài sản; category = chủng loại.")
NHAN_SYSTEM = ("Bạn đặt nhãn cột cho bảng số liệu báo cáo hành chính tiếng Việt. Nhãn là tiếng Việt có "
               "dấu đầy đủ, ngắn gọn, đúng nghĩa dữ liệu, không bao giờ là tên trường CSDL. Trả về JSON.")


def la_ten_truong(text: str) -> bool:
    """Chuỗi có dạng tên trường CSDL (không được hiện cho người đọc)."""
    return bool(_TEN_TRUONG.search(str(text or "")))


def _doc_duoc(text: str) -> bool:
    return bool(_DAU_VIET.search(text)) or (" " in text and text[:1].isupper())


def nhan_hop_le(label: Any, name: str = "") -> bool:
    text = " ".join(str(label or "").split())
    if not 2 <= len(text) <= 40 or la_ten_truong(text):
        return False
    # Cấm chép tên cột chỉ khi tên cột là tên trường ("soluong"); alias đã là tiếng Việt chuẩn
    # ("[Số nhân sự]") thì dùng lại chính nó là đúng.
    ten = " ".join(str(name or "").split())
    if text.lower() == ten.lower() and not _doc_duoc(ten):
        return False
    return _doc_duoc(text)


def don_vi_hop_le(unit: Any) -> bool:
    text = " ".join(str(unit or "").split())
    if not text:
        return True
    if len(text) > 15 or la_ten_truong(text):
        return False
    return not text.isascii() or text.lower() in _DON_VI_ASCII


def nfc(value: Any) -> Any:
    """Chữ tiếng Việt về dạng dựng sẵn (NFC). Unikey ở chế độ "Unicode tổ hợp" và dữ liệu nhập từ
    macOS cho chữ NFD - dấu là ký tự rời: font vẽ dấu lệch/tách khỏi chữ trên biểu đồ và slide,
    và "Phòng" NFD không bằng "Phòng" NFC khi so khớp."""
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


def _sach(text: Any) -> str:
    return nfc(" ".join(str(text or "").split()))


VAI_TRO = ("nhom", "so", "ky")
_KY = re.compile(r"^(?:(?:T|Th|Tháng|Q|Quý)\s?\d{1,2}(?:/\d{4})?|\d{4}-\d{2}|\d{1,2}/\d{4}|"
                 r"(?:19|20)\d{2})$", re.I)
# Ngày/thời điểm cụ thể (2026-09-15, 2026-09-15T08:00:00): thuộc tính của dòng, không phải kỳ báo cáo.
_NGAY = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$")


def _so(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%").strip())   # "12.5%" vẫn là số (tỷ lệ)
    except ValueError:
        return None


def _la_so(values: list[Any]) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and all(_so(v) is not None for v in present)


def tin_hieu_du_lieu(cols: list[dict[str, Any]], rows: list[list[Any]]) -> dict[int, list[str]]:
    """Dấu hiệu TẤT ĐỊNH đọc từ chính dữ liệu, cho Qwen làm căn cứ khi đặt nhãn/vai trò - Text-to-SQL không
    trả câu SQL, nên đây là cách duy nhất biết "cong_don" là cộng dồn chứ không phải "công đón"."""
    n = len(cols)
    col_vals = [[r[i] if i < len(r) else None for r in rows] for i in range(n)]
    so = {i: [(_so(v) or 0.0) for v in col_vals[i]] for i in range(n) if _la_so(col_vals[i])}
    out: dict[int, list[str]] = {i: [] for i in range(n)}
    for i in range(n):
        vals = [v for v in col_vals[i] if v is not None]
        if vals and all(_KY.match(str(v).strip()) for v in vals):
            out[i].append("giá trị có dạng kỳ thời gian (tháng/quý/năm)")
        if vals and all(_NGAY.match(str(v).strip()) for v in vals):
            out[i].append("giá trị là ngày/thời điểm cụ thể (không phải kỳ tháng/quý/năm)")
        if vals and all(str(v).strip().endswith("%") for v in vals):
            out[i].append("giá trị có ký hiệu % (tỷ lệ)")
        if i not in so:
            continue
        x = so[i]
        if len(x) >= 2:
            for j, y in so.items():
                if j != i and x != y and all(abs(x[k] - sum(y[:k + 1])) < 1e-9 for k in range(len(x))):
                    out[i].append(f"bằng tổng cộng dồn theo dòng của cột \"{cols[j]['name']}\"")
            khac = [j for j in so if j != i]
            if len(khac) >= 2 and all(abs(x[k] - sum(so[j][k] for j in khac)) < 1e-9 for k in range(len(x))):
                out[i].append("bằng tổng các cột số còn lại trên cùng dòng")
        if x and all(0 <= v <= 100 for v in x) and any(v != int(v) for v in x):
            out[i].append("mọi giá trị trong 0-100 và có số thập phân (có thể là tỷ lệ %)")
        if any(v < 0 for v in x):
            out[i].append("có giá trị âm")
    return out


def _cot_can_nhan(cols: list[dict[str, Any]]) -> list[int]:
    """Cột chưa có nhãn hợp lệ, trùng nhãn với cột trước, hoặc chưa có vai trò."""
    can, da_co = [], set()
    for i, c in enumerate(cols):
        label = _sach(c.get("label"))
        if (not nhan_hop_le(label, str(c.get("name") or "")) or label.lower() in da_co
                or c.get("role") not in VAI_TRO):
            can.append(i)
        else:
            da_co.add(label.lower())
    return can


def _prompt(cols: list[dict[str, Any]], rows: list[list[Any]], bang: str, question: str,
            can: list[int], loi: list[str], tin_hieu: dict[int, list[str]]) -> str:
    mau = [{"ten": c["name"], "kieu": c.get("data_type", ""), "nhan_hien_co": c.get("label") or "",
            "can_dat_nhan": i in can, "vi_du": [str(r[i])[:30] for r in rows[:5] if i < len(r)],
            "dau_hieu": tin_hieu.get(i, [])}
           for i, c in enumerate(cols)]
    sua = ("\nLần trước có chỗ không dùng được, sửa lại:\n- " + "\n- ".join(loi) + "\n") if loi else ""
    # Nội dung RIÊNG của bảng là căn cứ chính: yêu cầu chung có thể nhắc tới bảng khác ("thiết bị, đặt
    # cạnh số nhân sự") - chỉ đưa yêu cầu chung thì cột "so_luong" của bảng thiết bị thành "Số nhân sự".
    ngu_canh = (f"Yêu cầu chung của người dùng (chỉ để hiểu bối cảnh): {question[:300]}\n"
                if question and question != bang else "")
    return (f"Nội dung của bảng này: {bang[:300]}\n{ngu_canh}{THUAT_NGU}\nCác cột kết quả (theo thứ tự):\n"
            f"{json.dumps(mau, ensure_ascii=False)}\n{sua}\n"
            "\"dau_hieu\" do hệ thống tính từ toàn bộ dữ liệu, là SỰ THẬT về cột - nhãn phải khớp với nó "
            "(ví dụ cột bằng tổng cộng dồn của cột khác là cột cộng dồn/lũy kế).\n"
            "Với các cột có \"can_dat_nhan\": true, dựa vào NỘI DUNG CỦA BẢNG NÀY, tên cột, giá trị ví dụ và "
            "dấu hiệu:\n"
            "- \"nhan\": tiếng Việt có dấu đầy đủ, tối đa 30 ký tự, như tiêu đề cột báo cáo hành chính "
            "(\"Phòng ban\", \"Số nhân sự\", \"Nhân sự cuối tháng 8\"); KHÔNG chép tên cột không dấu, KHÔNG viết "
            "không dấu, KHÔNG viết tắt (\"TB\", \"SL\", \"NS\" - viết đủ \"Trung bình\", \"Số lượng\"...), không dấu "
            "gạch dưới; tên cột đã là tiếng Việt có dấu đọc được thì giữ nguyên làm nhãn; không trùng nhãn cột khác; cột tên/nhóm bỏ chữ \"Tên\" ở đầu; bảng dạng "
            "chỉ tiêu - giá trị (mỗi dòng một chỉ tiêu khác loại) thì cột số là \"Giá trị\";\n"
            "- tên cột chung chung (sl1, sl2, c1...) thì gán theo thứ tự các đại lượng được nêu trong nội dung bảng;\n"
            "- \"don_vi\": đơn vị đếm có dấu (người, thiết bị...), tiền ghi \"đồng\", tỷ lệ ghi \"%\", "
            "không rõ để rỗng;\n"
            "- \"vai_tro\": \"ky\" nếu cột là kỳ thời gian, \"so\" nếu là số liệu để vẽ/tính, \"nhom\" nếu là "
            "tên/mã/nhóm để phân loại (mã số dù là số vẫn là \"nhom\");\n"
            "- \"loai_so\" (chỉ cột \"so\", cột khác để null): \"phat_sinh\" (số phát sinh/đếm/số lượng: tuyển mới "
            "trong tháng, số thiết bị theo phòng ban), \"ton\" (số tồn/hiện có/cuối kỳ tại một thời điểm: nhân sự "
            "cuối tháng 8), \"ty_le\", \"trung_binh\", \"don_gia\", \"cong_don\" (lũy kế), \"chi_tieu_khac_loai\" "
            "(bảng chỉ tiêu - giá trị, mỗi dòng một chỉ tiêu khác loại; khi đó \"don_vi\" để rỗng).\n"
            'Trả về JSON: {"cot": [{"ten": "<tên cột y nguyên>", "nhan": "...", "don_vi": "...", '
            '"vai_tro": "nhom|so|ky", "loai_so": "phat_sinh|ton|ty_le|trung_binh|don_gia|cong_don|chi_tieu_khac_loai|null"}]}')


async def _hoi_qwen(cols: list[dict[str, Any]], rows: list[list[Any]], bang: str, question: str,
                    can: list[int], loi: list[str], tin_hieu: dict[int, list[str]]) -> dict[str, dict[str, Any]]:
    from app.services.llm import get_llm

    data = await get_llm().chat_json(
        [{"role": "system", "content": NHAN_SYSTEM},
         {"role": "user", "content": _prompt(cols, rows, bang, question, can, loi, tin_hieu)}],
        temperature=0.0, max_tokens=900, thinking=False)
    out: dict[str, dict[str, Any]] = {}
    for item in (data or {}).get("cot") or []:
        if isinstance(item, dict) and item.get("ten"):
            out[str(item["ten"])] = {"label": _sach(item.get("nhan")), "unit": _sach(item.get("don_vi")),
                                     "role": str(item.get("vai_tro") or "").strip().lower(),
                                     "kind": str(item.get("loai_so") or "").strip().lower()}
    return out


LOAI_SO = ("phat_sinh", "ton", "ty_le", "trung_binh", "don_gia", "cong_don", "chi_tieu_khac_loai")


def cong_duoc(kind: str, tin_hieu: list[str], co_truc_ky: bool) -> bool:
    """Cộng mọi dòng của cột thành một tổng có nghĩa không - quy tắc của CODE, Qwen chỉ phân loại cột.

    Số tồn ("nhân sự cuối tháng 8") cộng theo phòng ban ra tổng của đơn vị, nhưng cộng qua các kỳ thì
    vô nghĩa (cuối tháng 1 + cuối tháng 2). Bảng có trục kỳ hay không thì code biết, model không cần đoán.
    """
    if any(("cộng dồn" in t or "tỷ lệ" in t) for t in tin_hieu):
        return False                          # dấu hiệu tính từ dữ liệu thắng phân loại của model
    if kind == "phat_sinh":
        return True
    if kind == "ton":
        return not co_truc_ky
    return False


def lam_sach_ket_qua(result: dict[str, Any]) -> dict[str, Any]:
    """Chốt chặn CUỐI trước khi kết quả truy vấn lên Word/slide - đồng bộ, không gọi LLM, luôn chạy.

    Nhãn đặt ở `gan_nhan_cot` (Qwen). Ở đây chỉ KIỂM: nhãn nào vẫn là tên trường / không dấu / trùng
    nhau thì thay bằng nhãn trung tính "Cột n" - không đoán, không ánh xạ - và đưa mọi chữ về NFC.
    Sửa tại chỗ, trả lại chính `result`.
    """
    cols = result.get("columns") or []
    da_co: set[str] = set()
    for index, col in enumerate(cols):
        label = _sach(col.get("label"))
        if not nhan_hop_le(label, str(col.get("name") or "")) or label.lower() in da_co:
            label = f"Cột {index + 1}"
        da_co.add(label.lower())
        col["label"] = label
        unit = _sach(col.get("unit"))
        col["unit"] = unit if don_vi_hop_le(unit) else ""
        if col.get("role") not in VAI_TRO:
            col.pop("role", None)
    result["rows"] = [[nfc(v) for v in row] for row in result.get("rows") or []]
    if "question" in result:
        result["question"] = nfc(result.get("question"))
    return result


def _kiem_goi_y(g: dict[str, Any], name: str, la_so: bool, tin_hieu: list[str], co_nhom_chu: bool = False) -> str:
    """Lý do gợi ý của Qwen không dùng được (rỗng = dùng được). Chỉ kiểm điều code biết chắc."""
    if not nhan_hop_le(g.get("label"), name):
        return f"cột \"{name}\": nhãn \"{g.get('label', '')}\" không có dấu, là tên trường hoặc rỗng"
    if g.get("role") not in VAI_TRO:
        return f"cột \"{name}\": vai_tro phải là nhom, so hoặc ky"
    if g["role"] == "so" and not la_so:
        return f"cột \"{name}\": vai_tro \"so\" nhưng giá trị không phải số"
    if any("kỳ thời gian" in t for t in tin_hieu) and g["role"] != "ky":
        return f"cột \"{name}\": giá trị là kỳ thời gian, vai_tro phải là \"ky\""
    # Bảng đã nhóm theo một cột chữ (phòng ban...) thì cột ngày cụ thể là thuộc tính của dòng, không phải trục kỳ.
    if any("ngày/thời điểm cụ thể" in t for t in tin_hieu) and g["role"] == "ky" and co_nhom_chu:
        return (f"cột \"{name}\": giá trị là ngày cụ thể của từng dòng (bảng đã nhóm theo cột khác), "
                "vai_tro phải là \"nhom\"")
    if g["role"] == "so" and g.get("kind") not in LOAI_SO:
        return f"cột \"{name}\": cột số phải có loai_so là một trong {', '.join(LOAI_SO)}"
    return ""


async def gan_nhan_cot(result: dict[str, Any], question: str = "") -> dict[str, Any]:
    """Qwen đặt `label`/`unit`/`role`/`cong_duoc` cho các cột còn thiếu (sửa tại chỗ, trả lại).

    `question` là yêu cầu chung của người dùng (bối cảnh); căn cứ chính là nội dung riêng của bảng
    (`result["question"]`) và dấu hiệu tính từ dữ liệu. Gọi nhiều lần không sao: cột đã đủ thì không hỏi lại.
    """
    cols = result.get("columns") or []
    rows = result.get("rows") or []
    bang = str(result.get("question") or "").strip().split("\n")[0] or question
    tin_hieu = tin_hieu_du_lieu(cols, rows)
    loi: list[str] = []
    for lan in range(1, MAX_LAN_HOI + 1):
        can = _cot_can_nhan(cols)
        if not can:
            break
        try:
            goi_y = await _hoi_qwen(cols, rows, bang, question, can, loi, tin_hieu)
        except Exception as exc:  # noqa: BLE001 - thiếu Qwen thì còn nhãn trung tính, không làm hỏng tài liệu
            logger.warning("Không gọi được Qwen để đặt nhãn cột (lần %d): %s", lan, exc)
            break
        loi = []
        for i in can:
            col, name = cols[i], str(cols[i].get("name") or "")
            g = goi_y.get(name) or {}
            la_so = _la_so([r[i] if i < len(r) else None for r in rows])
            co_nhom_chu = any(j != i and not tin_hieu.get(j) and not _la_so([r[j] if j < len(r) else None for r in rows])
                              for j in range(len(cols)))
            if (ly_do := _kiem_goi_y(g, name, la_so, tin_hieu.get(i, []), co_nhom_chu)):
                loi.append(ly_do)
                continue
            label = g["label"]
            if g["role"] == "nhom" and re.match(r"^Tên \S", label) and nhan_hop_le(label[4:]):
                label = label[4:5].upper() + label[5:]   # quy tắc trình bày: "Phòng ban", không "Tên phòng ban"
            col["label"], col["role"] = label, g["role"]
            col["kind"] = g["kind"] if g["role"] == "so" else None
            if g.get("kind") == "chi_tieu_khac_loai":
                col["unit"] = ""              # mỗi dòng một chỉ tiêu khác loại: không có đơn vị chung
            elif not col.get("unit") and g.get("unit") and don_vi_hop_le(g["unit"]):
                col["unit"] = g["unit"]
        loi += [f"nhãn \"{cols[i]['label']}\" bị trùng với cột khác" for i in _cot_can_nhan(cols)
                if cols[i].get("role") in VAI_TRO and nhan_hop_le(cols[i].get("label"))]
    if (con := _cot_can_nhan(cols)):
        logger.warning("Còn %d cột chưa đủ nhãn/vai trò sau %d lần hỏi Qwen: %s", len(con), MAX_LAN_HOI,
                       [cols[i].get("name") for i in con])
    # Tính SAU khi mọi cột đã có vai trò: "số tồn" cộng được hay không tuỳ bảng có trục kỳ.
    co_truc_ky = any(c.get("role") == "ky" for c in cols)
    for i, col in enumerate(cols):
        if col.get("role") == "so" and col.get("kind") in LOAI_SO:
            col["cong_duoc"] = cong_duoc(col["kind"], tin_hieu.get(i, []), co_truc_ky)
    return lam_sach_ket_qua(result)


async def gan_nhan_moi_truy_van(data: dict[str, Any], question: str = "") -> dict[str, Any]:
    """Đặt nhãn cho MỌI kết quả trong `data["queries"]` trước khi dựng tài liệu. Dữ liệu có thể tới
    từ đường không qua bước lấy số liệu (agent nhiều bước, hội thoại cũ) - đặt nhãn ở cửa ra cho chắc."""
    for result in data.get("queries") or []:
        await gan_nhan_cot(result, question)
    return data
