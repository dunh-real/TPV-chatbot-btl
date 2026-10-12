"""Nhãn tiếng Việt cho cột kết quả truy vấn ERP, để Word/slide không in tên trường CSDL.

Text-to-SQL trả cột với tên alias do model đặt (`soluongnhansu`, `tenphongban`, `employee_count`)
và thường không kèm nhãn. Tên đó lọt thẳng lên tài liệu thì người đọc thấy cú pháp CSDL thay vì
"Số nhân sự (người)". Thứ tự lấy nhãn:

  1. nhãn có sẵn trong kết quả, nếu đã là chữ tiếng Việt đọc được;
  2. Qwen đặt nhãn + đơn vị cho các cột còn thiếu (một lượt gọi, có kiểm đầu ra);
  3. tách tên không dấu bằng từ điển ("soluongnhansu" -> "Số lượng nhân sự");
  4. nhãn trung tính "Cột n" - không bao giờ trả lại tên trường thô.
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

# Âm tiết tiếng Việt không dấu thường gặp trong tên cột ERP -> chữ có dấu.
_AM_TIET = {
    "so": "số", "luong": "lượng", "nhan": "nhân", "su": "sự", "vien": "viên", "phong": "phòng", "ban": "ban",
    "ten": "tên", "ma": "mã", "tong": "tổng", "danh": "danh", "muc": "mục", "thiet": "thiết", "bi": "bị",
    "tai": "tài", "san": "sản", "don": "đơn", "vi": "vị", "ngay": "ngày", "thang": "tháng", "nam": "năm",
    "chuc": "chức", "vu": "vụ", "trang": "trạng", "thai": "thái", "gia": "giá", "tri": "trị", "nhom": "nhóm",
    "loai": "loại", "dang": "đang", "lam": "làm", "viec": "việc", "moi": "mới", "nghi": "nghỉ",
    "tuyen": "tuyển", "hop": "hợp", "dong": "đồng", "ty": "tỷ", "le": "lệ", "trung": "trung", "binh": "bình",
    "khach": "khách", "hang": "hàng", "doanh": "doanh", "thu": "thu", "chi": "chi", "phi": "phí",
    "gioi": "giới", "tinh": "tính", "tuoi": "tuổi", "cap": "cấp", "bac": "bậc", "ky": "kỳ", "quy": "quý",
    "bo": "bộ", "phan": "phận", "cong": "công", "vat": "vật", "tu": "tư", "kho": "kho",
    "hieu": "hiệu", "luc": "lực", "hoat": "hoạt", "xe": "xe", "may": "máy",
    "vao": "vào", "ra": "ra", "hoc": "học", "trinh": "trình", "do": "độ", "dia": "địa",
    "lien": "liên", "he": "hệ", "nghiep": "nghiệp", "dau": "đầu", "cuoi": "cuối", "con": "còn", "lai": "lại",
    "tien": "tiền", "gio": "giờ", "luot": "lượt", "mua": "mua", "lan": "lần", "diem": "điểm",
    "phat": "phát", "ton": "tồn", "roi": "rời", "khoi": "khỏi", "tang": "tăng", "giam": "giảm",
    "tieu": "tiêu", "nhap": "nhập", "xuat": "xuất", "hien": "hiện", "han": "hạn", "ngoai": "ngoài", "trong": "trong",
}
# Từ ghép có nghĩa riêng khi đứng cạnh nhau (âm tiết trùng chữ cần dấu khác).
_GHEP = {"congty": "công ty", "hoatdong": "hoạt động", "tinhtrang": "tình trạng", "diachi": "địa chỉ",
         "nhanvien": "nhân viên", "nhansu": "nhân sự", "phongban": "phòng ban",
         "chungloai": "chủng loại", "chitieu": "chỉ tiêu", "hocvan": "học vấn", "thamnien": "thâm niên", "biendong": "biến động",
         "chuyendong": "chuyển động", "sudung": "sử dụng", "capphat": "cấp phát"}
# Từ tiếng Anh hay gặp trong alias.
_ANH = {"count": "Số lượng", "total": "Tổng", "name": "Tên", "department": "Phòng ban", "employee": "Nhân viên",
        "employees": "Nhân viên", "asset": "Tài sản", "assets": "Tài sản", "category": "Danh mục",
        "quantity": "Số lượng", "code": "Mã", "date": "Ngày", "month": "Tháng", "year": "Năm",
        "status": "Trạng thái", "amount": "Số tiền", "value": "Giá trị", "type": "Loại", "id": "Mã"}

NHAN_SYSTEM = ("Bạn đặt nhãn cột cho bảng số liệu hành chính. Chỉ viết tiếng Việt có dấu, ngắn gọn, "
               "đúng nghĩa dữ liệu. Trả về JSON.")


def la_ten_truong(text: str) -> bool:
    """Chuỗi có dạng tên trường CSDL (không được hiện cho người đọc)."""
    return bool(_TEN_TRUONG.search(str(text or "")))


def nhan_hop_le(label: Any, name: str = "") -> bool:
    text = " ".join(str(label or "").split())
    if not 2 <= len(text) <= 40 or text.lower() == str(name).lower() or la_ten_truong(text):
        return False
    return bool(_DAU_VIET.search(text)) or (" " in text and text[:1].isupper())


def _tach_am_tiet(word: str) -> list[str] | None:
    """Tách chuỗi không dấu viết liền thành âm tiết có trong từ điển (ưu tiên khớp dài)."""
    word = word.lower()
    keys = sorted(set(_AM_TIET) | set(_GHEP), key=len, reverse=True)
    out, i = [], 0
    while i < len(word):
        for k in keys:
            if word.startswith(k, i):
                out.append(k)
                i += len(k)
                break
        else:
            return None
    return out


def nhan_tu_ten(name: str) -> str:
    """Nhãn dựng từ chính tên cột; rỗng nếu không dựng được nhãn tử tế."""
    parts = [p for p in re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", str(name or "")) if p]
    if parts and all(p.lower() in _ANH for p in parts):
        # Tiếng Anh đặt từ chính ở cuối ("employee_count"), tiếng Việt đặt ở đầu ("Số lượng nhân viên").
        label = " ".join(_ANH[p.lower()].lower() for p in reversed(parts))
        return label[:1].upper() + label[1:]
    words: list[str] = []
    run = ""            # các phần không dấu liền nhau, tách âm tiết chung: "tinh_trang" khớp từ ghép "tình trạng"

    def flush() -> bool:
        nonlocal run
        if run:
            syl = _tach_am_tiet(run)
            if syl is None:
                return False
            words.extend(_GHEP.get(s) or _AM_TIET[s] for s in syl)
            run = ""
        return True

    for p in parts:
        low = p.lower()
        if low.isdigit() or low in _ANH:
            if not flush():
                return ""
            # "cuoi_thang_8" -> "Cuối tháng 8"
            words.append(low if low.isdigit() else _ANH[low].lower())
            continue
        run += low
    if not flush():
        return ""
    label = " ".join(words).strip()
    return label[:1].upper() + label[1:] if label else ""


def don_vi_mac_dinh(label: str) -> str:
    low = label.lower()
    if re.search(r"(số|tổng).*(nhân sự|nhân viên|người|lao động)", low):
        return "người"
    return ""


async def _hoi_qwen(cols: list[dict[str, Any]], rows: list[list[Any]], question: str) -> dict[str, dict[str, str]]:
    from app.services.llm import get_llm

    mau = [{"ten": c["name"], "kieu": c.get("data_type", ""),
            "vi_du": [str(r[i])[:30] for r in rows[:3] if i < len(r)]} for i, c in enumerate(cols)]
    prompt = (f"Câu hỏi của người dùng: {question[:300]}\nCác cột kết quả:\n{json.dumps(mau, ensure_ascii=False)}\n\n"
              "Đặt cho mỗi cột một nhãn tiếng Việt có dấu, tối đa 30 ký tự, viết như tiêu đề cột trong báo cáo "
              "hành chính (ví dụ: \"Phòng ban\", \"Mã phòng ban\", \"Số nhân sự\"). Cột tên/nhóm thì bỏ chữ \"Tên\" "
              "ở đầu (\"Phòng ban\" thay vì \"Tên phòng ban\"). Cột số đếm thì ghi đơn vị đếm (người, tài sản, "
              "thiết bị...), cột tiền ghi \"đồng\", không rõ thì để rỗng.\n"
              'Trả về JSON: {"cot": [{"ten": "<tên cột y nguyên>", "nhan": "...", "don_vi": "..."}]}')
    data = await get_llm().chat_json(
        [{"role": "system", "content": NHAN_SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=600, thinking=False)
    out: dict[str, dict[str, str]] = {}
    for item in (data or {}).get("cot") or []:
        if isinstance(item, dict) and item.get("ten"):
            out[str(item["ten"])] = {"label": " ".join(str(item.get("nhan") or "").split()),
                                     "unit": " ".join(str(item.get("don_vi") or "").split())[:15]}
    return out


def nfc(value: Any) -> Any:
    """Chữ tiếng Việt về dạng dựng sẵn (NFC). Unikey ở chế độ "Unicode tổ hợp" và dữ liệu nhập từ
    macOS cho chữ NFD - dấu là ký tự rời: font vẽ dấu lệch/tách khỏi chữ trên biểu đồ và slide,
    và "Phòng" NFD không bằng "Phòng" NFC khi so khớp."""
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


_DON_VI_ASCII = {"kg", "km", "m2", "m3", "usd", "vnd", "đ", "%", "h", "gb", "tb", "mb"}


def nhan_an_toan(col: dict[str, Any], index: int) -> str:
    """Nhãn chắc chắn đọc được: nhãn sẵn có nếu hợp lệ, không thì từ điển, cuối cùng "Cột n"."""
    name = str(col.get("name") or "")
    label = nfc(" ".join(str(col.get("label") or "").split()))
    if nhan_hop_le(label, name):
        return label
    tu = nhan_tu_ten(name)
    if tu and nhan_hop_le(tu, name):
        tu = re.sub(r"^Tên (?=\S)", "", tu)
        return tu[:1].upper() + tu[1:]
    return f"Cột {index + 1}"


def lam_sach_ket_qua(result: dict[str, Any]) -> dict[str, Any]:
    """Chốt chặn CUỐI trước khi kết quả truy vấn lên Word/slide - không gọi LLM, luôn chạy.

    `gan_nhan_cot` (có Qwen) chạy ở bước lấy số liệu, nhưng dữ liệu còn có thể tới bằng đường khác
    (agent nhiều bước, kết quả cũ trong hội thoại, test). Ở đây không cho bất kỳ nhãn cột nào là tên
    trường CSDL hay chữ không dấu ("soluong", "thangnam") đi tiếp, và đưa mọi chữ về NFC.
    Sửa tại chỗ, trả lại chính `result`.
    """
    for index, col in enumerate(result.get("columns") or []):
        col["label"] = nhan_an_toan(col, index)
        unit = nfc(" ".join(str(col.get("unit") or "").split()))
        if la_ten_truong(unit) or (unit.isascii() and unit.lower() not in _DON_VI_ASCII):
            unit = ""        # "nguoi", "so_luong": đơn vị không dấu/tên trường thì bỏ, không in sai
        col["unit"] = unit or don_vi_mac_dinh(col["label"])
    result["rows"] = [[nfc(v) for v in row] for row in result.get("rows") or []]
    if "question" in result:
        result["question"] = nfc(result.get("question"))
    return result


async def gan_nhan_cot(result: dict[str, Any], question: str = "") -> dict[str, Any]:
    """Điền `label`/`unit` cho mọi cột của một kết quả `QueryResult.as_dict()` (sửa tại chỗ, trả lại)."""
    cols = result.get("columns") or []
    thieu = [c for c in cols if not nhan_hop_le(c.get("label"), c.get("name", ""))]
    if not thieu:
        return lam_sach_ket_qua(result)
    goi_y: dict[str, dict[str, str]] = {}
    try:
        goi_y = await _hoi_qwen(cols, result.get("rows") or [], question or str(result.get("question") or ""))
    except Exception as exc:  # noqa: BLE001 - thiếu Qwen vẫn còn từ điển và nhãn trung tính
        logger.warning("Không gọi được Qwen để đặt nhãn cột: %s", exc)
    for index, col in enumerate(cols):
        if col in thieu:
            name = str(col.get("name") or "")
            g = goi_y.get(name) or {}
            if nhan_hop_le(g.get("label"), name):
                col["label"] = g["label"]
                if g.get("unit") and not la_ten_truong(g["unit"]) and not col.get("unit"):
                    col["unit"] = g["unit"]
            elif (tu := nhan_tu_ten(name)) and nhan_hop_le(tu, name):
                tu = re.sub(r"^Tên (?=\S)", "", tu)          # cột tên/nhóm: "Phòng ban", không "Tên phòng ban"
                col["label"] = tu[:1].upper() + tu[1:]
            else:
                col["label"] = f"Cột {index + 1}"
        if not col.get("unit"):
            col["unit"] = don_vi_mac_dinh(str(col.get("label") or ""))
    return lam_sach_ket_qua(result)
