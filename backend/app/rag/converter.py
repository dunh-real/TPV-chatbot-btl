"""Chuyển file nguồn thành Markdown sạch trước khi chunk.

Mọi định dạng đều quy về Markdown vì đó là dạng giữ được bảng biểu và cấu trúc
tiêu đề - thứ mà trích xuất text thuần làm mất:

    .pdf              trang digital -> pymupdf4llm (bảng thành bảng Markdown)
                      trang scan    -> OCR bằng VLM (app.rag.ocr)
    ảnh               -> OCR
    .docx .xlsx .pptx -> MarkItDown
    .csv .json        -> MarkItDown
    .txt .md          -> đọc thẳng
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.ocr import IMAGE_SUFFIXES, OCRService, get_ocr_service
from app.rag.tieng_viet import sua_dinh_chu, von_tu_cua

logger = logging.getLogger(__name__)

OFFICE_SUFFIXES = {".docx", ".doc", ".xlsx", ".pptx", ".csv", ".json"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES | OFFICE_SUFFIXES | TEXT_SUFFIXES

PAGE_SEPARATOR = "\n\n"

_PAGE_NUMBER_LINE = re.compile(r"^[-_\s]*\d{1,4}[-_\s]*$")
_SEPARATOR_LINE = re.compile(r"^[_=*\-]{3,}$")        # không khớp "|---|---|" của bảng
_UPPERCASE_HEADING = re.compile(r"^[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s\d,./()-]{4,}$")
# Dòng mở/đóng khối bố cục do `app.documents.bo_cuc_hanh_chinh` sinh ra.
_DONG_BO_CUC = re.compile(r"^:::(\s|$)")
# Chữ khuôn của văn bản hành chính: viết hoa nhưng KHÔNG phải tiêu đề mục. Quốc
# hiệu và tiêu ngữ là phần đầu thư, ngang hàng với tên cơ quan ban hành - gán
# chúng thành `##` thì con dấu và quốc hiệu to ngang tên văn bản, và tệ hơn:
# `app.rag.chunking` cắt chunk theo heading nên mỗi tờ công văn bị cắt bậy ngay
# tại quốc hiệu, rồi lấy chính dòng đó làm nhãn chương cho nội dung bên dưới.
_CHU_KHUON = (
    "cong hoa xa hoi chu nghia viet nam",
    "doc lap - tu do - hanh phuc",
    "doc lap – tu do – hanh phuc",
    "cong van den",
)


def _nfc(text: str) -> str:
    """Dựng sẵn dạng NFC. DOCX soạn bằng một số bộ gõ lưu chữ có dấu dạng tổ hợp (NFD):
    regex cụm chữ coi dấu tổ hợp là ký tự ngắt, nên "Đường" bị tách thành "Đư ờng"."""
    return unicodedata.normalize("NFC", text)


# Tiêu đề Điều in đậm dài hơn giới hạn của `_BOLD_LINE` vẫn còn nguyên `**...**`,
# và mọi mẫu neo `^Điều` trượt qua nó (Bộ luật Hình sự: Điều 5, 6, ... mất ranh giới).
_BOLD_DIEU = re.compile(r"^\*\*(?P<inner>(?:Điều|ĐIỀU)\s+\d+[a-z]?\s*[.:].*?)\*\*[:.]?$")


def _la_chu_khuon(dong: str) -> bool:
    """Dòng khuôn của văn bản hành chính, so khớp sau khi bỏ dấu."""
    bo_dau = unicodedata.normalize("NFD", dong.lower())
    bo_dau = "".join(c for c in bo_dau if unicodedata.category(c) != "Mn")
    bo_dau = bo_dau.replace("đ", "d").strip()
    return any(mau in bo_dau for mau in _CHU_KHUON)
_BOLD_LINE = re.compile(r"^\*\*(?P<inner>[^*]{3,150})\*\*[:.]?$")
_TABLE_LINE = re.compile(r"^\s*\|")

# Thẻ mũ/chỉ số dưới mà bộ trích xuất bê nguyên từ PDF LaTeX sang Markdown.
_THE_MU = re.compile(r"</?su[pb]>")
# Nhấn mạnh bằng MỘT gạch dưới - LaTeX in nghiêng mọi ký hiệu toán, nên công thức
# tới đây thành "_L_", "_αi_", "_H_<sup>¯</sup>". Hai neo âm chặn hai đầu dính chữ
# cái, để tên biến "ten_bien_nay" không bị gỡ mất gạch dưới; chữ số thì vẫn cho
# qua vì "10<sup>_−_3</sup>" phải về được thành "10−3".
_NGHIENG_GACH = re.compile(r"(?<![^\W\d_])_([^_\n]{1,80})_(?![^\W\d_])")


@dataclass(slots=True)
class LoadedDocument:
    """Markdown đã làm sạch, kèm mốc ký tự bắt đầu mỗi trang (nếu nguồn có trang)."""

    text: str
    page_offsets: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Làm sạch
# --------------------------------------------------------------------------- #
def fix_broken_bold(line: str) -> str:
    """Gỡ dấu ** bị vỡ vụn trên văn bản tiếng Việt.

    pymupdf4llm phát hiện in đậm theo từng span font; chữ có dấu thường nằm ở span
    khác nên một từ bị xé thành "**Đi**ề**u**". Dòng nào có nhiều hơn một cặp **
    thì gần như chắc chắn là vỡ chứ không phải in đậm thật.
    """
    if line.count("**") > 2:
        return line.replace("**", "")
    return line


def go_markup_cong_thuc(line: str) -> str:
    """Gỡ vỏ Markdown/HTML mà PDF LaTeX để lại quanh ký hiệu toán.

        "_H_<sup>¯</sup> _i_"  ->  "H¯ i"
        "Sau _L_ bước"         ->  "Sau L bước"

    Không phải chuyện thẩm mỹ. Ngữ cảnh gửi cho model là chữ này y nguyên, và
    model chép lại y nguyên: màn hình người dùng hiện ra "H_<sup>ˉ</sup> _i_"
    giữa một câu tiếng Việt. Ở đây không cần in nghiêng - chunk chỉ cần chữ.
    """
    return _NGHIENG_GACH.sub(r"\1", _THE_MU.sub("", line))


def cleanup_markdown(text: str) -> str:
    """Bỏ rác của khâu trích xuất và chuẩn hoá tiêu đề, giữ nguyên khối bảng."""
    lines_out: list[str] = []
    trong_khoi_bo_cuc = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = go_markup_cong_thuc(fix_broken_bold(raw_line)).rstrip()
        stripped = line.strip()

        # Khối bố cục đi qua nguyên vẹn. Bên trong nó chữ đã được xếp đúng chỗ
        # rồi; chuẩn hoá thêm lần nữa chỉ phá - "ĐƠN KHỞI KIỆN" nằm trong khối
        # căn giữa mà bị gán `##` thì vừa hỏng khối, vừa mất căn giữa.
        if _DONG_BO_CUC.match(stripped):
            # "::: <tên>" mở khối, ":::" trơn đóng khối.
            trong_khoi_bo_cuc = stripped != ":::"
            lines_out.append(stripped)
            continue
        if trong_khoi_bo_cuc:
            lines_out.append(stripped)
            continue

        # Dòng trống: giữ tối đa một dòng trống liên tiếp (ranh giới đoạn).
        if not stripped:
            if lines_out and lines_out[-1] != "":
                lines_out.append("")
            continue

        # Bảng phải được giữ nguyên xi, kể cả khoảng trắng căn cột.
        if _TABLE_LINE.match(line):
            lines_out.append(line)
            continue

        if _PAGE_NUMBER_LINE.match(stripped) or _SEPARATOR_LINE.match(stripped):
            continue

        if stripped.startswith("#"):
            khong_rao = stripped.replace("**", "")
            # Bộ trích xuất PDF cũng tự gắn `#` cho chữ to/đậm, nên quốc hiệu có
            # thể tới đây khi ĐÃ là heading - hạ nó xuống, cùng lý do như dưới.
            if _la_chu_khuon(khong_rao):
                khong_rao = khong_rao.lstrip("#").strip()
            lines_out.append(khong_rao)
            continue

        if (dieu := _BOLD_DIEU.match(stripped)) is not None:
            lines_out.append(f"### {dieu.group('inner').strip()}")
            continue

        # Tiêu đề ngầm: dòng ngắn viết hoa toàn bộ, hoặc dòng chỉ gồm **in đậm**.
        if len(stripped) < 200 and not _la_chu_khuon(stripped):
            if _UPPERCASE_HEADING.match(stripped):
                lines_out.append(f"## {stripped}")
                continue
            if (bold := _BOLD_LINE.match(stripped)) is not None:
                lines_out.append(f"### {bold.group('inner').strip()}")
                continue

        lines_out.append(line)

    cleaned = "\n".join(lines_out)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# Tỷ lệ trang phải cùng mang một dòng thì mới coi là tiêu đề chạy.
TY_LE_TIEU_DE_CHAY = 0.6
# Số trang tối thiểu để phép đếm có nghĩa.
TRANG_TOI_THIEU = 3
# Dòng dài hơn mức này là nội dung, không phải tiêu đề chạy.
DAI_TIEU_DE_CHAY = 120
# Chỉ soi vài dòng sát mép trên và mép dưới mỗi trang.
SO_DONG_MEP = 2


def _dong_mep(trang: str) -> list[str]:
    """Vài dòng không rỗng sát mép trên và mép dưới của một trang."""
    dong = [d.strip() for d in trang.split("\n") if d.strip()]
    return dong[:SO_DONG_MEP] + dong[-SO_DONG_MEP:]


def bo_tieu_de_chay(page_texts: list[str]) -> list[str]:
    """Bỏ dòng tiêu đề/chân trang lặp lại ở mép mọi trang.

    Tên bài, tên tác giả, tên cơ quan in lại ở đầu mỗi trang là chữ của KHUÔN
    trang, không phải của nội dung. Giữ chúng thì mỗi chunk mở đầu bằng cùng một
    dòng, nên đoạn trích dẫn nào cũng khoe tên tài liệu trước khi tới ý chính -
    đúng thứ người dùng nhìn thấy trong ô "Nguồn tham khảo". Tệ hơn: dòng lặp
    đó vào cả embedding, kéo mọi chunk của tài liệu xích lại gần nhau.
    """
    if len(page_texts) < TRANG_TOI_THIEU:
        return page_texts

    dem: Counter[str] = Counter()
    for trang in page_texts:
        dem.update(set(_dong_mep(trang)))
    nguong = max(TRANG_TOI_THIEU, int(len(page_texts) * TY_LE_TIEU_DE_CHAY))
    lap = {dong for dong, so in dem.items()
           if so >= nguong and len(dong) <= DAI_TIEU_DE_CHAY}
    if not lap:
        return page_texts

    ra: list[str] = []
    for trang in page_texts:
        # Chỉ bỏ ở MÉP trang. Cùng một dòng nằm giữa trang là nội dung thật.
        mep = set(_dong_mep(trang)) & lap
        giu = [d for d in trang.split("\n") if d.strip() not in mep]
        ra.append(re.sub(r"\n{3,}", "\n\n", "\n".join(giu)).strip())
    return ra


# --------------------------------------------------------------------------- #
# Trích xuất trang digital
# --------------------------------------------------------------------------- #
def extract_digital_pages(path: Path, page_count: int) -> list[str]:
    """Markdown của từng trang PDF, lấy thẳng từ lớp text có sẵn (không OCR).

    Trả về đúng `page_count` phần tử: trang nào không lấy được thì là chuỗi rỗng,
    để chỉ số trong danh sách luôn khớp số trang thật.
    """
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return [""]
    try:
        import pymupdf4llm

        pages = pymupdf4llm.to_markdown(
            str(path), page_chunks=True, show_progress=False, table_strategy="lines_strict"
        )
    except Exception as exc:  # noqa: BLE001 - còn đường lùi là OCR
        logger.warning("pymupdf4llm lỗi trên %s: %s", path.name, exc)
        return [""] * page_count

    texts = [""] * page_count
    for index, page in enumerate(pages[:page_count]):
        texts[index] = tach_moc_cau_truc((page.get("text") or "").strip())
    _bu_lop_chu_an(path, texts)
    return texts


def _bu_lop_chu_an(path: Path, texts: list[str]) -> None:
    """Trang scan có lớp chữ OCR ẩn của nơi phát hành (chữ alpha = 0 phủ trên ảnh): classify_pages coi là
    digital, nhưng pymupdf4llm bỏ chữ ẩn -> cả trang chỉ còn dòng dấu ký số (NĐ 379/2026 trang 1: 43 ký tự
    thay vì 1650, mất quốc hiệu, số hiệu, Chương I, Điều 1). Khi đó lấy thẳng lớp chữ theo khối đoạn văn."""
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            for index, text in enumerate(texts):
                if index >= doc.page_count:
                    break
                blocks = doc[index].get_text("blocks", sort=True)
                raw = "\n".join(" ".join(b[4].split()) for b in blocks if b[6] == 0 and b[4].strip())
                if len(raw) > 200 and len(text) < 0.5 * len(raw):
                    texts[index] = tach_moc_cau_truc(raw)
    except Exception as exc:  # noqa: BLE001 - giữ kết quả pymupdf4llm
        logger.warning("Không đọc được lớp chữ của %s: %s", path.name, exc)


# Mốc cấu trúc VBQPPL bị pymupdf4llm nối vào giữa đoạn: PDF căn đều, không giãn dòng giữa các
# đoạn nên cả trang thành vài dòng dài (TT 25/2026/TT-BNV: 10 trang -> 22 dòng, "**Điều 2. …** 1. Đối
# với …" chung một dòng) - chunker không thấy Điều/Khoản nào. Chỉ tách khi mốc đứng ngay sau dấu kết
# câu/vế (". ; :" hoặc "**"), để viện dẫn giữa câu ("tại khoản 1 Điều 5") giữ nguyên.
_MOC_CAU_TRUC = re.compile(
    r"(?<=[.;:”\"*)])[ \t]+(?=(?:\*\*)?(?:Điều \d+[a-z]?\.\s|(?:Chương|CHƯƠNG) [IVXLC]+\b|(?:Mục|MỤC) \d+\.?\s"
    r"|\d{1,2}\.\s+[A-ZĐÀ-Ỹ]|[a-zđ]\)\s))"
    r"|[ \t]+(?=\*\*Điều \d+[a-z]?\.)")       # tiêu đề Điều in đậm: tách cả khi trước nó là số trang/dấu ký số
_THE_DANH_DAU = re.compile(r"</?mark>")


# Chữ Kirin trùng hình chữ Latinh mà mô hình OCR đôi khi in ra ("379/2026/NĐ-CР" với Р = U+0420):
# nhìn giống hệt nhưng mọi so khớp chữ (số hiệu, BM25, tra địa chỉ) đều trượt. VBQPPL tiếng Việt
# không dùng chữ Kirin, nên quy hết về Latinh.
_KIRIN_GIA = str.maketrans("АВЕКМНОРСТХаеорсухіІ", "ABEKMHOPCTXaeopcyxiI")


def bo_chu_kirin_gia(text: str) -> str:
    return text.translate(_KIRIN_GIA)


def tach_moc_cau_truc(text: str) -> str:
    return _MOC_CAU_TRUC.sub("\n", _THE_DANH_DAU.sub("", text))


# --------------------------------------------------------------------------- #
# DOCX -> Markdown từ cây khối của parse_docx
# --------------------------------------------------------------------------- #
_DONG_KHOAN = re.compile(r"^\d{1,3}[a-z]?\.\s")


def _bang_markdown(text: str) -> str:
    rows = [r for r in text.split("\n") if r.strip(" |")]
    if not rows:
        return ""
    cells = [[c.strip() for c in r.split(" | ")] for r in rows]
    width = max(len(c) for c in cells)
    out = ["| " + " | ".join(c + [""] * (width - len(c))) + " |" for c in cells]
    out.insert(1, "|" + " --- |" * width)
    return "\n".join(out)


def docx_blocks_markdown(path: Path) -> str:
    """Markdown tối thiểu dựng từ `parse_docx` (cấu trúc khối đã có cho workflow soát văn bản),
    thay MarkItDown: rag_testing M2 - cùng chất lượng truy hồi, chuyển đổi nhanh 2,2-3,4x, bộ nhớ
    ít hơn ~13x. Đoạn in đậm ngắn không kết câu (tiêu đề) thành **...**; dòng Khoản thì không."""
    from app.documents.parser import parse_docx

    parts: list[str] = []
    for b in parse_docx(path).blocks:
        text = _nfc(b.text)
        if b.kind == "table":
            parts.append(_bang_markdown(text))
        elif b.kind == "heading":
            parts.append(f"### {text}")
        elif b.bold and len(text) < 200 and "\n" not in text and not text.endswith((".", ";", ",")) \
                and not _DONG_KHOAN.match(text):
            parts.append(f"**{text}**")
        else:
            parts.append(text)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #
class DocumentConverter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._markitdown = None
        self._ocr: OCRService | None = None

    @property
    def ocr(self) -> OCRService:
        """Dịch vụ OCR ĐÚNG theo cấu hình của converter này.

        Trước đây chỗ này gọi thẳng `get_ocr_service()`, tức singleton đọc từ .env
        - nên `DocumentConverter(Settings(ocr_enabled=False))` vẫn gọi mô hình
        thật. Một bài test tưởng mình chạy khép kín hoá ra phụ thuộc vào chữ mà
        model trả về hôm đó, và đỏ khi ai đó chỉnh tham số lấy mẫu.

        Cấu hình toàn cục thì vẫn dùng singleton, để cả tiến trình chia chung một
        thread pool thay vì mỗi nơi dựng một cái.
        """
        if self._ocr is None:
            self._ocr = (get_ocr_service() if self.settings is get_settings()
                         else OCRService(self.settings))
        return self._ocr

    def convert(self, path: str | Path) -> LoadedDocument:
        path = Path(path)
        suffix = path.suffix.lower()

        if suffix == ".pdf" or suffix in IMAGE_SUFFIXES:
            return self._convert_pdf(path)
        if suffix == ".doc":
            # MarkItDown không đọc được Word 97-2003 -> chuyển sang .docx trước.
            from app.rag.doc_convert import doc_to_docx

            path = doc_to_docx(path, self.settings)
        if suffix in OFFICE_SUFFIXES:
            # Không qua `sua_dinh_chu`: bộ đọc Office lấy chữ từ XML nên KHÔNG có lỗi
            # dính chữ của PDF; chạy nó ở đây chỉ gây hại - tách "kilôgam" thành
            # "ki lô gam", "Hêrôin" thành "Hê rô in" (đo trên Bộ luật Hình sự).
            if path.suffix.lower() == ".docx" and self.settings.docx_converter == "blocks":
                try:
                    return LoadedDocument(text=cleanup_markdown(docx_blocks_markdown(path)))
                except Exception as exc:  # noqa: BLE001 - còn đường lùi MarkItDown
                    logger.warning("Dựng Markdown từ khối DOCX lỗi trên %s, dùng MarkItDown: %s", path.name, exc)
            return LoadedDocument(text=cleanup_markdown(_nfc(self._convert_office(path))))
        if suffix in TEXT_SUFFIXES:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            return LoadedDocument(text=sua_dinh_chu(cleanup_markdown(_nfc(raw))))

        raise ValueError(
            f"Định dạng chưa hỗ trợ: {path.suffix} "
            f"(hỗ trợ: {', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )

    # ------------------------------------------------------------ office -- #
    def _convert_office(self, path: Path) -> str:
        if self._markitdown is None:
            from markitdown import MarkItDown

            self._markitdown = MarkItDown()
        result = self._markitdown.convert(str(path))
        return result.text_content or ""

    # --------------------------------------------------------------- pdf -- #
    def _convert_pdf(self, path: Path) -> LoadedDocument:
        """Trích xuất theo từng trang rồi ghép, giữ lại mốc trang cho trích dẫn."""
        ocr = self.ocr
        needs_ocr = ocr.classify_pages(path)
        if not needs_ocr:
            return LoadedDocument(text="")

        page_texts = extract_digital_pages(path, len(needs_ocr))

        scan_pages = [i for i, need in enumerate(needs_ocr) if need]
        if scan_pages:
            if ocr.enabled:
                # OCR chỉ ghi đè khi thực sự đọc được chữ, tránh xoá mất nội dung
                # mà bản digital đã lấy được.
                for page_number, text in ocr.ocr_pages(path, scan_pages).items():
                    page_texts[page_number] = text
            else:
                logger.warning(
                    "%s có %d trang scan nhưng OCR đang tắt (OCR_ENABLED=false) - "
                    "những trang này sẽ thiếu nội dung",
                    path.name, len(scan_pages),
                )

        cleaned = bo_tieu_de_chay([cleanup_markdown(bo_chu_kirin_gia(text)) for text in page_texts])
        # Vốn từ phải lấy từ CẢ tài liệu chứ không riêng từng trang: chữ dính ở
        # trang này gần như luôn đứng rời ở trang khác, và đó là bằng chứng để
        # chọn đúng chỗ cắt. Sửa vẫn theo từng trang để mốc trang không lệch.
        von = von_tu_cua("\n".join(cleaned))
        return self._join_pages([sua_dinh_chu(page, von) for page in cleaned])

    @staticmethod
    def _join_pages(page_texts: list[str]) -> LoadedDocument:
        parts: list[str] = []
        offsets: list[int] = []
        cursor = 0
        for text in page_texts:
            # Trang rỗng vẫn phải chiếm một mốc để số trang không bị lệch.
            offsets.append(cursor)
            parts.append(text)
            cursor += len(text) + len(PAGE_SEPARATOR)
        return LoadedDocument(text=PAGE_SEPARATOR.join(parts).strip(), page_offsets=offsets)


_converter: DocumentConverter | None = None


def get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter
