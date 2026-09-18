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
    "trich_yeu": (re.compile(r"(?:^|\s)V/v\s+(.+)", re.I), 1),
    "kinh_gui": (re.compile(r"^\s*Kính\s+gửi\s*:\s*(.*)", re.I | re.M), 1),
    "noi_nhan": (re.compile(r"^\s*Nơi\s+nhận\s*:", re.I | re.M), None),
    "chu_ky": (
        # Chức danh người ký rất đa dạng: GIÁM ĐỐC, TRƯỞNG ĐƠN VỊ, CHỈ HUY TRƯỞNG,
        # CHÁNH VĂN PHÒNG, hoặc ký thay/ký thừa lệnh (TM./KT./TL.).
        re.compile(r"^\s*((?:PHÓ\s*)?(?:GIÁM\s*ĐỐC|CHÁNH\s*VĂN\s*PHÒNG)"
                   r"|(?:[A-ZÀ-Ỹ]+\s+)?(?:THỦ\s*)?TRƯỞNG(?:\s+[A-ZÀ-Ỹ]+){0,3}"
                   r"|TM\.\s*.+|KT\.\s*.+|TL\.\s*.+|\(đã\s*ký\))\s*$", re.I | re.M), 1),
    "ten_loai": (
        re.compile(r"^\s*(CÔNG\s*VĂN|QUYẾT\s*ĐỊNH|THÔNG\s*BÁO|BÁO\s*CÁO|TỜ\s*TRÌNH|"
                   r"KẾ\s*HOẠCH|BIÊN\s*BẢN|GIẤY\s*MỜI|CHỈ\s*THỊ|NGHỊ\s*QUYẾT)\s*$", re.I | re.M), 1),
}

# Dạng số ký hiệu hợp lệ: "105/CV-BGĐ", "12/QĐ-TPV", "07/2026/TT-BTC"
SO_KY_HIEU_RE = re.compile(r"^\d+(?:/\d{4})?/[A-ZĐ]{2,}(?:-[A-ZĐ0-9.]+)*$", re.I)


def detect_components(structure: DocumentStructure) -> DocumentComponents:
    """Quét các khối, ghi nhận thành phần đầu tiên khớp mỗi mẫu."""
    components = DocumentComponents()

    for block in structure.blocks:
        if block.is_empty:
            continue
        for component_id, (pattern, group) in _PATTERNS.items():
            if component_id in components.found:
                continue
            match = pattern.search(block.text)
            if match is None:
                continue
            value = ""
            if group is not None:
                value = (match.group(group) if group else match.group(0)).strip()
            components.found[component_id] = Component(
                id=component_id, block_id=block.id, text=block.text.strip(), value=value
            )

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
