"""Dò các thành phần thể thức của văn bản hành chính Việt Nam.

Theo Nghị định 30/2020/NĐ-CP, một văn bản hành chính gồm các thành phần: quốc hiệu
- tiêu ngữ, tên cơ quan ban hành, số và ký hiệu, địa danh và thời gian ban hành,
tên loại và trích yếu, nội dung, chức vụ - chữ ký, dấu, nơi nhận.

Ở đây chỉ dò *vị trí* của từng thành phần trong cây khối; việc phán xét đúng/thiếu
là của rule engine, theo tham số trong file YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documents.parser import Block, DocumentStructure


@dataclass(slots=True)
class Component:
    """Một thành phần thể thức đã dò được."""

    id: str
    block_id: str
    text: str
    value: str = ""        # phần trích ra được: số ký hiệu, ngày tháng, trích yếu...


@dataclass(slots=True)
class DocumentComponents:
    found: dict[str, Component] = field(default_factory=dict)

    def has(self, component_id: str) -> bool:
        return component_id in self.found

    def get(self, component_id: str) -> Component | None:
        return self.found.get(component_id)

    def value_of(self, component_id: str) -> str:
        component = self.found.get(component_id)
        return component.value if component else ""


# Mỗi thành phần: (regex nhận diện, nhóm trích giá trị nếu có)
_PATTERNS: dict[str, tuple[re.Pattern[str], int | None]] = {
    "quoc_hieu": (# "HÒA" và "HOÀ" đều hợp lệ - dấu có thể đặt trên O hoặc A.
        re.compile(r"CỘNG\s*H[OÒ][AÀ]\s*XÃ\s*HỘI\s*CHỦ\s*NGHĨA\s*VIỆT\s*NAM", re.I), None),
    "tieu_ngu": (re.compile(r"Độc\s*lập\s*[-–]\s*Tự\s*do\s*[-–]\s*Hạnh\s*phúc", re.I), None),
    "so_ky_hieu": (re.compile(r"^\s*Số\s*:\s*(\S+)", re.I | re.M), 1),
    "dia_danh_ngay": (
        re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I), 0),
    # "V/v Kiểm kê..." và "V/v: Kiểm kê..." đều gặp trong thực tế.
    "trich_yeu": (re.compile(r"(?:^|\s)V/v\s*:?\s*(.+)", re.I), 1),
    "kinh_gui": (re.compile(r"^\s*Kính\s+gửi\s*:\s*(.*)", re.I | re.M), 1),
    "noi_nhan": (re.compile(r"^\s*Nơi\s+nhận\s*:", re.I | re.M), None),
    # Chỗ dành sẵn; mẫu thật do build_chu_ky_pattern dựng từ danh sách chức danh
    # trong file tiêu chí - xem DEFAULT_CHUC_DANH_KY.
    "chu_ky": (re.compile(r"(?!)"), 1),
    "ten_loai": (
        re.compile(r"^\s*(CÔNG\s*VĂN|QUYẾT\s*ĐỊNH|THÔNG\s*BÁO|BÁO\s*CÁO|TỜ\s*TRÌNH|"
                   r"KẾ\s*HOẠCH|BIÊN\s*BẢN|GIẤY\s*MỜI|CHỈ\s*THỊ|NGHỊ\s*QUYẾT)\s*$", re.I | re.M), 1),
}

# Dạng số ký hiệu hợp lệ: "105/CV-BGĐ", "12/QĐ-TPV", "07/2026/TT-BTC"
SO_KY_HIEU_RE = re.compile(r"^\d+(?:/\d{4})?/[A-ZĐ]{2,}(?:-[A-ZĐ0-9.]+)*$", re.I)

# Chức danh người ký khác nhau theo từng cơ quan: doanh nghiệp ký "TỔNG GIÁM ĐỐC",
# đơn vị quân đội ký "CHỈ HUY TRƯỞNG", trường học ký "HIỆU TRƯỞNG". Danh sách này
# chỉ là mặc định - cơ quan nào dùng chức danh khác thì thêm vào `chuc_danh_ky`
# trong file tiêu chí, không phải sửa code.
DEFAULT_CHUC_DANH_KY: tuple[str, ...] = (
    "GIÁM ĐỐC", "TỔNG GIÁM ĐỐC", "PHÓ GIÁM ĐỐC", "PHÓ TỔNG GIÁM ĐỐC",
    "CHÁNH VĂN PHÒNG", "PHÓ CHÁNH VĂN PHÒNG", "HIỆU TRƯỞNG", "CHỦ TỊCH",
)

# Hình thức ký thay / ký thừa lệnh / quyền: cố định theo NĐ 30 nên không đưa ra
# cấu hình. "(đã ký)" là bản sao scan hoặc bản điện tử.
_KY_THAY_RE = r"TM\.\s*.+|KT\.\s*.+|TL\.\s*.+|Q\.\s*.+|\(đã\s*ký\)"

# "TRƯỞNG PHÒNG KỸ THUẬT", "CHỈ HUY TRƯỞNG", "THỦ TRƯỞNG ĐƠN VỊ"...
_TRUONG_RE = r"(?:[A-ZÀ-Ỹ]+\s+)?(?:THỦ\s*)?TRƯỞNG(?:\s+[A-ZÀ-Ỹ]+){0,3}"


def build_chu_ky_pattern(titles: list[str] | tuple[str, ...] | None = None) -> re.Pattern[str]:
    """Mẫu nhận dòng chức danh người ký, dựng từ danh sách chức danh cho trước."""
    names = [re.escape(t.strip()).replace(r"\ ", r"\s*") for t in (titles or DEFAULT_CHUC_DANH_KY) if t.strip()]
    # Chức danh dài đặt trước để "PHÓ GIÁM ĐỐC" không bị "GIÁM ĐỐC" ăn mất.
    names.sort(key=len, reverse=True)
    alternatives = "|".join([*names, _TRUONG_RE, _KY_THAY_RE])
    return re.compile(rf"^\s*({alternatives})\s*$", re.I | re.M)


# Mẫu mặc định nằm sẵn trong _PATTERNS; bộ tiêu chí nào khai chức danh riêng thì
# `_patterns_for` dựng một bản sao cho riêng lần quét đó. Không sửa _PATTERNS tại
# chỗ: hai request dùng hai bộ tiêu chí khác nhau sẽ giẫm lên nhau.
_PATTERNS["chu_ky"] = (build_chu_ky_pattern(None), 1)


def _patterns_for(chu_ky_titles: list[str] | None) -> dict[str, tuple[re.Pattern[str], int | None]]:
    if not chu_ky_titles:
        return _PATTERNS
    return {**_PATTERNS, "chu_ky": (build_chu_ky_pattern(chu_ky_titles), 1)}


# Văn bản CÓ tên loại (báo cáo, quyết định, tờ trình...) đặt trích yếu ngay dưới
# tên loại và mở đầu bằng "Về ...", không dùng dạng "V/v" của công văn.
_TRICH_YEU_DUOI_TEN_LOAI = re.compile(r"^\s*(?:V/v|Về)\b\s*:?\s*(.+)", re.I | re.S)

_TABLE_BLOB_RE = re.compile(r"^T\d+")


def _scan(text: str, component_id: str, patterns) -> tuple[bool, str]:
    """Khớp một mẫu trên một đoạn text, trả về (có khớp, giá trị trích ra)."""
    pattern, group = patterns[component_id]
    match = pattern.search(text)
    if match is None:
        return False, ""
    if group is None:
        return True, ""
    return True, (match.group(group) if group else match.group(0)).strip()


def _trich_yeu_duoi_ten_loai(
    structure: DocumentStructure, components: DocumentComponents
) -> Component | None:
    """Trích yếu của văn bản có tên loại: khối kế tiếp ngay dưới tên loại."""
    ten_loai = components.get("ten_loai")
    if ten_loai is None:
        return None
    ids = [b.id for b in structure.blocks]
    if ten_loai.block_id not in ids:
        return None

    # Cùng một khối: file .md/.txt không tách "BÁO CÁO" và "Về tình hình..."
    # thành hai đoạn, cả hai nằm chung một khối.
    lines = ten_loai.text.splitlines()
    name = (ten_loai.value or "").strip().upper()
    for idx, line in enumerate(lines):
        if line.strip().upper() != name:
            continue
        rest = "\n".join(lines[idx + 1:]).strip()
        match = _TRICH_YEU_DUOI_TEN_LOAI.match(rest)
        if match is not None:
            value = " ".join(match.group(1).split())
            if len(value) <= 300:
                return Component(id="trich_yeu", block_id=ten_loai.block_id,
                                 text=rest, value=value)
        break

    taken = {c.block_id for c in components.found.values()}
    for block in structure.blocks[ids.index(ten_loai.block_id) + 1:]:
        if block.is_empty:
            continue
        if block.id in taken:
            return None
        match = _TRICH_YEU_DUOI_TEN_LOAI.match(block.text.strip())
        if match is None:
            # Ngay dưới tên loại đã là nội dung khác -> văn bản thiếu trích yếu thật.
            return None
        value = " ".join(match.group(1).split())
        # Trích yếu là một mệnh đề, không phải cả đoạn văn: dài quá thì gần như
        # chắc chắn đã bắt nhầm phần nội dung.
        if len(value) > 300:
            return None
        return Component(id="trich_yeu", block_id=block.id,
                         text=block.text.strip(), value=value)
    return None


def detect_components(
    structure: DocumentStructure, chu_ky_titles: list[str] | None = None
) -> DocumentComponents:
    """Quét các khối, ghi nhận thành phần đầu tiên khớp mỗi mẫu."""
    patterns = _patterns_for(chu_ky_titles)
    components = DocumentComponents()

    for block in structure.blocks:
        if block.is_empty:
            continue
        for component_id in patterns:
            if component_id in components.found:
                continue
            hit, value = _scan(block.text, component_id, patterns)
            if hit:
                components.found[component_id] = Component(
                    id=component_id, block_id=block.id, text=block.text.strip(), value=value
                )

    # Khối bảng là một blob gộp mọi ô bằng " | ", nên mẫu neo đầu dòng (chức danh
    # người ký chẳng hạn) trượt hết. Quét lại từng ô cho những thành phần còn thiếu,
    # nhưng vẫn ghi block_id là blob để `format_block` lần xuống đúng ô.
    missing = [cid for cid in patterns if cid not in components.found]
    if missing:
        for cell in structure.table_cells:
            for component_id in list(missing):
                hit, value = _scan(cell.text, component_id, patterns)
                if not hit:
                    continue
                blob = _TABLE_BLOB_RE.match(cell.id)
                components.found[component_id] = Component(
                    id=component_id,
                    block_id=blob.group(0) if blob else cell.id,
                    text=cell.text.strip(),
                    value=value,
                )
                missing.remove(component_id)

    if "trich_yeu" not in components.found:
        if (found := _trich_yeu_duoi_ten_loai(structure, components)) is not None:
            components.found["trich_yeu"] = found

    return components


def format_block(structure: DocumentStructure, component: Component) -> Block | None:
    """Khối mang định dạng THẬT của một thành phần thể thức.

    Quốc hiệu và tiêu ngữ gần như luôn nằm trong ô bảng - đó là cách dựng bố cục
    hai cột "tên cơ quan | quốc hiệu". Khối bảng lại là một blob gộp mọi ô và lấy
    định dạng của ô ĐẦU TIÊN, tức tên cơ quan. Phán xét cỡ chữ hay độ đậm của quốc
    hiệu trên blob đó thì sai chắc chắn, nên ở đây đi xuống đúng ô chứa nó.

    Trả None khi không lần được - rule engine sẽ báo bỏ qua, không báo lỗi.
    """
    block = structure.block(component.block_id)
    if block is None or block.kind != "table":
        return block

    pattern = _PATTERNS.get(component.id, (None, None))[0]
    if pattern is None:
        return None
    for cell in structure.table_cells:
        if cell.id.startswith(component.block_id) and pattern.search(cell.text):
            return cell
    return None


def parse_ngay_ban_hanh(value: str) -> tuple[int, int, int] | None:
    """"ngày 05 tháng 9 năm 2026" -> (5, 9, 2026)."""
    match = _PATTERNS["dia_danh_ngay"][0].search(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def body_blocks(structure: DocumentStructure, components: DocumentComponents) -> list[Block]:
    """Các khối thuộc phần nội dung - đầu vào cho bước soát chính tả/câu văn.

    Bỏ phần thể thức đầu và cuối văn bản: soát chính tả trên "CỘNG HÒA XÃ HỘI CHỦ
    NGHĨA VIỆT NAM" hay "Nơi nhận:" chỉ tạo ra báo động giả.
    """
    skip_ids = {
        component.block_id
        for key, component in components.found.items()
        if key in ("quoc_hieu", "tieu_ngu", "so_ky_hieu", "dia_danh_ngay",
                   "ten_loai", "chu_ky", "noi_nhan")
    }

    start = 0
    if (kinh_gui := components.get("kinh_gui")) is not None:
        ids = [b.id for b in structure.blocks]
        if kinh_gui.block_id in ids:
            start = ids.index(kinh_gui.block_id) + 1

    end = len(structure.blocks)
    if (noi_nhan := components.get("noi_nhan")) is not None:
        ids = [b.id for b in structure.blocks]
        if noi_nhan.block_id in ids:
            end = ids.index(noi_nhan.block_id)

    return [b for b in structure.blocks[start:end] if b.id not in skip_ids and not b.is_empty]
