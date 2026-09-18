"""Cắt Markdown thành chunk theo cấu trúc, đo độ dài bằng token của embedder.

Thứ tự ưu tiên khi cắt: heading > khối bảng > đoạn văn > câu. Bốn ngưỡng token
điều khiển toàn bộ quá trình:

    chunk_min_tokens   (200)  dưới mức này là mảnh vụn -> gộp vào chunk kề
    chunk_ideal_tokens (700)  kích thước nhắm tới khi đóng gói
    chunk_max_tokens   (1200) vượt mức này thì phải cắt tiếp
    chunk_hard_cap     (1500) trần tuyệt đối, cắt cưỡng bức

Bảng biểu được giữ nguyên khối; bảng dài hơn trần thì cắt theo dòng nhưng **lặp
lại dòng tiêu đề** ở mỗi phần, nếu không thì "500.000" ở chunk sau không còn biết
mình thuộc cột nào.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings

# Heading markdown, "Điều 12.", "Chương II", "1.2.3 Tiêu đề", "PHỤ LỤC A"
_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+"
    r"|(?:Điều|ĐIỀU)\s+\d+[.:]?.*"
    r"|(?:Chương|CHƯƠNG|Mục|MỤC|Phần|PHẦN|Phụ lục|PHỤ LỤC)\s+[IVXLCDM\d]+[.:]?.*"
    r"|\d+(?:\.\d+)*\.?\s+[A-ZÀ-Ỹ].*"
    r"|[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s\d,./-]{6,}"
    r")$",
    re.MULTILINE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?…;])\s+(?=[\"'(\[]?[A-ZÀ-Ỹ0-9])|\n+")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_BLOCK = re.compile(r"((?:^[ \t]*\|.*\|[ \t]*$\n?)+)", re.MULTILINE)
_TABLE_DIVIDER = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_CHAPTER_RE = re.compile(
    r"^(?:#\s|(?:Chương|CHƯƠNG|Phần|PHẦN|Phụ lục|PHỤ LỤC)\s)", re.IGNORECASE
)


@dataclass(slots=True)
class Chunk:
    text: str
    index: int
    section: str = ""
    chapter: str = ""          # heading cấp 1 gần nhất - ranh giới không được gộp qua
    token_count: int = 0
    has_table: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
    # Mọi mục mà chunk này chứa (smart merge có thể gộp nhiều mục nhỏ lại).
    sections: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.sections and self.section:
            self.sections = [self.section]

    @property
    def section_label(self) -> str:
        """Nhãn mục dùng cho trích dẫn: một mục, hoặc phạm vi "đầu - cuối"."""
        named = [s for s in self.sections if s]
        if not named:
            return ""
        if len(named) == 1:
            return named[0]
        return f"{named[0]} - {named[-1]}"


def _default_token_counter() -> Callable[[str], int]:
    """Đếm token bằng chính tokenizer của embedding model (nạp lười)."""

    def count(text: str) -> int:
        from app.rag.embedding import get_embedder

        return len(get_embedder().tokenizer.encode(text, add_special_tokens=False))

    return count


def approximate_token_counter(text: str) -> int:
    """Xấp xỉ nhanh khi chưa muốn nạp model: ~1.6 token / âm tiết tiếng Việt."""
    return max(1, int(len(text.split()) * 1.6))


def contains_table(text: str) -> bool:
    return any(_TABLE_LINE.match(line) for line in text.splitlines())


def is_table_block(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and all(_TABLE_LINE.match(line) for line in lines)


def isolate_tables(text: str) -> str:
    """Bọc mỗi khối bảng bằng dòng trống để nó thành một đơn vị độc lập."""
    return _TABLE_BLOCK.sub(lambda m: f"\n\n{m.group(1).strip()}\n\n", text)


def table_header(lines: list[str]) -> list[str]:
    """Dòng tiêu đề của bảng markdown: dòng đầu + dòng phân cách nếu có."""
    if not lines:
        return []
    if len(lines) > 1 and _TABLE_DIVIDER.match(lines[1]):
        return lines[:2]
    return lines[:1]


class Chunker:
    def __init__(
        self,
        settings: Settings | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._count = token_counter or _default_token_counter()

    # ---------------------------------------------------------------- API -- #
    def split(self, text: str, base_metadata: dict[str, object] | None = None) -> list[Chunk]:
        text = isolate_tables(self._normalize(text))
        if not text:
            return []

        pieces: list[Chunk] = []
        for section_title, chapter, body in self._iter_sections(text):
            for piece in self._split_section(section_title, body):
                pieces.append(
                    Chunk(
                        text=piece,
                        index=0,
                        section=section_title,
                        chapter=chapter,
                        token_count=self._count(piece),
                        has_table=contains_table(piece),
                        metadata=dict(base_metadata or {}),
                    )
                )

        merged = self._smart_merge(pieces)
        for index, chunk in enumerate(merged):
            chunk.index = index
        return merged

    # ------------------------------------------------------------ nội bộ -- #
    @staticmethod
    def _normalize(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Gộp khoảng trắng thừa, nhưng chừa dòng bảng để không phá căn cột.
        lines = [
            line if _TABLE_LINE.match(line) else re.sub(r"[ \t]+", " ", line)
            for line in text.split("\n")
        ]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    def _iter_sections(self, text: str) -> list[tuple[str, str, str]]:
        """Trả về (tiêu đề mục, chương chứa nó, nội dung)."""
        matches = [m for m in _HEADING_RE.finditer(text) if not _TABLE_LINE.match(m.group())]
        if not matches:
            return [("", "", text)]

        sections: list[tuple[str, str, str]] = []
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", "", preamble))

        chapter = ""
        for i, match in enumerate(matches):
            title = match.group().lstrip("#").strip()
            if _CHAPTER_RE.match(match.group().strip()) or self._looks_like_chapter(title):
                chapter = title
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip()
            if body:
                sections.append((title, chapter, body))
        return sections

    @staticmethod
    def _looks_like_chapter(title: str) -> bool:
        return title.isupper() and len(title) > 6

    def _split_section(self, section_title: str, body: str) -> list[str]:
        cfg = self.settings
        # Tiêu đề mục được nhắc lại trong mọi chunk nên phải trừ trước vào hạn mức,
        # nếu không chunk sẽ vượt trần đúng bằng độ dài tiêu đề.
        overhead = self._count(section_title) + 1 if section_title else 0
        max_tokens = max(cfg.chunk_max_tokens - overhead, 1)
        ideal_tokens = max(cfg.chunk_ideal_tokens - overhead, 1)

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for unit in self._atomic_units(body, max_tokens):
            unit_tokens = self._count(unit)
            # Đóng gói tới ngưỡng lý tưởng; chỉ vượt lên khi đơn vị kế tiếp còn vừa trần.
            if current and (
                current_tokens >= ideal_tokens
                or current_tokens + unit_tokens > max_tokens
            ):
                chunks.append(self._render(section_title, current))
                current, current_tokens = self._carry_over(current, cfg.chunk_overlap)
                # Phần chồng lấn không được đẩy chunk mới vượt trần ngay từ đầu.
                if current_tokens + unit_tokens > max_tokens:
                    current, current_tokens = [], 0
            current.append(unit)
            current_tokens += unit_tokens

        if current:
            chunks.append(self._render(section_title, current))
        return chunks

    def _atomic_units(self, body: str, max_tokens: int) -> list[str]:
        """Đơn vị nhỏ nhất: khối bảng, hoặc đoạn văn; đoạn quá dài thì xuống mức câu."""
        units: list[str] = []
        for paragraph in (p.strip() for p in body.split("\n\n")):
            if not paragraph:
                continue

            if is_table_block(paragraph):
                units.extend(self._split_table(paragraph, max_tokens))
                continue

            if self._count(paragraph) <= max_tokens:
                units.append(paragraph)
                continue

            sentences = [s.strip() for s in _SENTENCE_RE.split(paragraph) if s and s.strip()]
            units.extend(sentences or [paragraph])
        return units

    def _split_table(self, table: str, max_tokens: int) -> list[str]:
        """Bảng dài thì cắt theo dòng, mỗi phần mang lại dòng tiêu đề của bảng."""
        if self._count(table) <= max_tokens:
            return [table]

        lines = [line for line in table.splitlines() if line.strip()]
        header = table_header(lines)
        rows = lines[len(header) :]
        header_tokens = self._count("\n".join(header))

        parts: list[str] = []
        current: list[str] = []
        current_tokens = header_tokens
        for row in rows:
            row_tokens = self._count(row)
            if current and current_tokens + row_tokens > max_tokens:
                parts.append("\n".join(header + current))
                current, current_tokens = [], header_tokens
            current.append(row)
            current_tokens += row_tokens
        if current:
            parts.append("\n".join(header + current))
        return parts

    def _carry_over(self, units: list[str], overlap_tokens: int) -> tuple[list[str], int]:
        """Giữ lại phần cuối chunk trước làm phần chồng lấn cho chunk sau."""
        carried: list[str] = []
        total = 0
        for unit in reversed(units):
            # Không lặp lại cả một khối bảng ở chunk kế tiếp.
            if is_table_block(unit):
                break
            unit_tokens = self._count(unit)
            if total + unit_tokens > overlap_tokens:
                break
            carried.insert(0, unit)
            total += unit_tokens
        return carried, total

    def _smart_merge(self, chunks: list[Chunk]) -> list[Chunk]:
        """Gộp mảnh vụn vào chunk kề, nhưng không gộp xuyên chương.

        Mảnh nhỏ kiểu "Điều 5. (đã bãi bỏ)" đứng riêng chỉ làm nhiễu truy hồi; gộp
        chúng lại giúp mỗi chunk đủ ngữ cảnh để reranker chấm điểm có nghĩa.
        """
        if not chunks:
            return []

        cfg = self.settings
        merged: list[Chunk] = [chunks[0]]
        for chunk in chunks[1:]:
            previous = merged[-1]
            total = previous.token_count + chunk.token_count
            same_chapter = previous.chapter == chunk.chapter

            should_merge = same_chapter and total <= cfg.chunk_hard_cap and (
                previous.token_count < cfg.chunk_min_tokens
                or chunk.token_count < cfg.chunk_min_tokens
                or total <= cfg.chunk_ideal_tokens
            )

            if should_merge:
                previous.text = f"{previous.text}\n\n{chunk.text}"
                previous.token_count = total
                previous.has_table = previous.has_table or chunk.has_table
                if not previous.section:
                    previous.section = chunk.section
                # Nhớ mọi mục đã gộp để trích dẫn không chỉ nêu mỗi mục đầu.
                for section in chunk.sections:
                    if section and section not in previous.sections:
                        previous.sections.append(section)
            else:
                merged.append(chunk)
        return merged

    @staticmethod
    def _render(section_title: str, units: list[str]) -> str:
        body = "\n\n".join(units).strip()
        # Nhắc lại tiêu đề mục trong chunk: giúp cả embedding lẫn LLM định vị.
        if not section_title or body.startswith(section_title):
            return body
        return f"{section_title}\n{body}".strip()


def chunk_text(
    text: str,
    metadata: dict[str, object] | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> list[Chunk]:
    return Chunker(token_counter=token_counter).split(text, metadata)
