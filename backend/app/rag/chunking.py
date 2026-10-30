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
_DIEU_NUM = re.compile(r"^Điều\s+(\d+[a-z]?)")
# Dòng mở đầu Khoản / Điểm trong thân Điều.
_KHOAN_LINE = re.compile(r"^\d{1,3}[a-z]?\.\s+\S")
_DIEM_LINE = re.compile(r"^[a-zđ]\d?\)\s+\S")


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
    # Địa chỉ pháp lý của từng mục đã gộp (chỉ có khi mục lấy từ legal_grammar):
    # {"dieu": "17", "chuong": "Chương III ...", "muc": ..., "phan": ..., "annex": bool}
    units: list[dict[str, object]] = field(default_factory=list)

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


_KHOAN_RE = re.compile(r"^\d{1,3}[a-z]?\.\s")


def _dieus(chunk: Chunk) -> set[str]:
    return {str(u.get("dieu")) for u in chunk.units if u.get("dieu")}


class Chunker:
    def __init__(
        self,
        settings: Settings | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._count = token_counter or _default_token_counter()
        self._legal: Chunker | None = None

    # ---------------------------------------------------------------- API -- #
    def count_tokens(self, text: str) -> int:
        """Số token theo đúng tokenizer của embedding model."""
        return self._count(text)

    def split(self, text: str, base_metadata: dict[str, object] | None = None) -> list[Chunk]:
        text = isolate_tables(self._normalize(text))
        if not text:
            return []

        sections = self._iter_sections(text)
        # VBQPPL chia theo Khoản dùng bộ ngưỡng riêng; mọi văn bản khác giữ ngưỡng chung.
        worker = self._legal_worker() if self._by_khoan and any(len(s) > 3 and s[3] for s in sections) else self
        pieces: list[Chunk] = []
        for section in sections:
            section_title, chapter, body = section[:3]
            unit = section[3] if len(section) > 3 else None
            for piece in worker._split_section(section_title, body, lead_carry=unit is not None):
                pieces.append(
                    Chunk(
                        text=piece,
                        index=0,
                        section=section_title,
                        chapter=chapter,
                        token_count=self._count(piece),
                        has_table=contains_table(piece),
                        metadata=dict(base_metadata or {}),
                        units=[dict(unit)] if unit else [],
                    )
                )

        merged = worker._smart_merge(pieces)
        for index, chunk in enumerate(merged):
            chunk.index = index
        return merged

    @property
    def _by_khoan(self) -> bool:
        return getattr(self.settings, "chunk_legal_structure", False) and \
            getattr(self.settings, "legal_chunk_granularity", "dieu") == "khoan"

    def _legal_worker(self) -> Chunker:
        if self._legal is None:
            cfg = self.settings
            self._legal = Chunker(cfg.model_copy(update={
                "chunk_min_tokens": cfg.legal_chunk_min_tokens, "chunk_ideal_tokens": cfg.legal_chunk_ideal_tokens,
                "chunk_max_tokens": cfg.legal_chunk_max_tokens, "chunk_hard_cap": cfg.legal_chunk_hard_cap}),
                self._count)
        return self._legal

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

    def _iter_sections(self, text: str) -> list[tuple]:
        """Trả về (tiêu đề mục, chương chứa nó, nội dung[, địa chỉ pháp lý])."""
        if getattr(self.settings, "chunk_legal_structure", False):
            legal = self._legal_sections(text)
            if legal is not None:
                return self._split_khoan(legal) if self._by_khoan else legal

        matches = [m for m in _HEADING_RE.finditer(text) if not _TABLE_LINE.match(m.group())]
        if not matches:
            return [("", "", text)]

        sections: list[tuple[str, str, str]] = []
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", "", preamble))

        chapter = ""
        # Heading có thân rỗng (ngay sau nó là heading khác) KHÔNG được bỏ: chữ của nó
        # chỉ sống trong tiêu đề mục, bỏ mục là mất luôn chữ. Dồn nó vào đầu mục kế tiếp.
        pending: list[str] = []
        for i, match in enumerate(matches):
            title = match.group().lstrip("#").strip()
            if _CHAPTER_RE.match(match.group().strip()) or self._looks_like_chapter(title):
                chapter = title
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[match.end() : end].strip()
            if not body:
                pending.append(title)
                continue
            if pending:
                body = "\n\n".join([*pending, body])
                pending = []
            sections.append((title, chapter, body))
        if pending:
            sections.append(("", chapter, "\n\n".join(pending)))
        return sections

    def _legal_sections(self, text: str) -> list[tuple] | None:
        """Ranh giới mục theo văn phạm VBQPPL (`legal_grammar`); None nếu không phải VBQPPL.

        Chỉ Phần/Chương/Mục/Điều CỦA CHÍNH VĂN BẢN mở mục; Khoản, Điểm và nội dung trích
        dẫn (luật sửa đổi) là thân mục. Tiêu đề mục là dòng "Điều n. Tên điều" nên
        `_render` lặp nó ở mọi chunk của Điều; `chapter` là Chương nên `_smart_merge`
        không gộp xuyên Chương. Dòng Phần/Chương/Mục được đưa vào đầu thân Điều kế tiếp
        (không bỏ chữ nào). Phụ lục sau Điều cuối mở mục riêng.
        """
        from app.legal.structure import scan

        sc = scan(text, units=("OWN_PHAN", "OWN_CHUONG", "OWN_MUC", "OWN_DIEU", "ANNEX"))
        if sc is None:
            return None
        lines, heads, consumed = sc.lines, sc.heads, sc.consumed

        sections: list[tuple] = []
        ctx = {"phan": "", "chuong": "", "muc": ""}
        title, chapter, body, unit = "", "", [], None
        pending: list[str] = []

        def close() -> None:
            if any(b.strip() for b in body):
                sections.append((title, chapter, "\n".join(body).strip(), unit))
            elif title:
                # Điều ngắn có toàn bộ nội dung ngay trên dòng tiêu đề vẫn là nội dung.
                sections.append(("", chapter, title, unit))

        for i, line in enumerate(lines):
            if i in consumed:
                continue
            h = heads.get(i)
            if h is None:
                body.append(line)
                continue
            # legal_grammar đã nối tên Phần/Chương/Mục (dòng HOA kế tiếp) vào h.text; scan()
            # đánh dấu các dòng đó (consumed) để không lặp lại trong thân.
            name = " ".join(h.text.split())
            if h.unit == "Điều":
                close()
                num = _DIEU_NUM.match(name)
                unit = {"unit": "Điều", "dieu": num.group(1) if num else "", "title": name, **ctx}
                title, body, pending = name, list(pending), []
            elif h.unit == "Phụ lục":
                close()
                unit = {"unit": "Phụ lục", "dieu": "", "title": name, "annex": True, **ctx}
                title, chapter, body = name, "Phụ lục", []
            else:
                if h.unit in ("Phần", "Chương"):
                    close()
                    title, body, unit = "", [], None
                key = {"Phần": "phan", "Chương": "chuong", "Mục": "muc"}[h.unit]
                ctx[key] = name
                if h.unit == "Phần":
                    ctx["chuong"] = ctx["muc"] = ""
                elif h.unit == "Chương":
                    ctx["muc"] = ""
                if h.unit == "Chương" or (h.unit == "Phần" and not chapter):
                    chapter = name
                pending.append(name)
        body.extend(pending)
        close()
        return sections

    @staticmethod
    def _split_khoan(sections: list[tuple]) -> list[tuple]:
        """Mỗi Điều chia theo Khoản (Điểm đi cùng Khoản của nó), cùng tiêu đề "Điều n. Tên" nên
        `_render` lặp tiêu đề Điều ở mọi chunk. Điều không có Khoản giữ nguyên một mục."""
        out: list[tuple] = []
        for sec in sections:
            title, chapter, body = sec[:3]
            unit = sec[3] if len(sec) > 3 else None
            if not unit or unit.get("unit") != "Điều":
                out.append(sec)
                continue
            groups: list[list[str]] = [[]]
            for para in body.split("\n\n"):
                if _KHOAN_RE.match(para.strip()) and groups[-1]:
                    groups.append([])
                groups[-1].append(para)
            if len(groups) == 1:
                out.append(sec)
                continue
            out.extend((title, chapter, g_body, unit) for g in groups if (g_body := "\n\n".join(g).strip()))
        return out

    @staticmethod
    def _looks_like_chapter(title: str) -> bool:
        return title.isupper() and len(title) > 6

    def _split_section(self, section_title: str, body: str, lead_carry: bool = False) -> list[str]:
        cfg = self.settings
        # Tiêu đề mục được nhắc lại trong mọi chunk nên phải trừ trước vào hạn mức,
        # nếu không chunk sẽ vượt trần đúng bằng độ dài tiêu đề.
        overhead = self._count(section_title) + 1 if section_title else 0
        max_tokens = max(cfg.chunk_max_tokens - overhead, 1)
        ideal_tokens = max(cfg.chunk_ideal_tokens - overhead, 1)

        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0

        khoan_lead = ""          # câu dẫn của Khoản đang mở ("2. Phạm tội thuộc ... sau đây:")
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
                # Cắt giữa danh sách Điểm: chunk mới phải mang câu dẫn của Khoản, không thì
                # "c) Có tổ chức" đứng một mình, mất cả mức hình phạt lẫn điều kiện áp dụng.
                if (lead_carry and khoan_lead and _DIEM_LINE.match(unit)
                        and khoan_lead not in current):
                    lead_tokens = self._count(khoan_lead)
                    if lead_tokens + current_tokens + unit_tokens <= max_tokens:
                        current.insert(0, khoan_lead)
                        current_tokens += lead_tokens
            if lead_carry and _KHOAN_LINE.match(unit):
                khoan_lead = unit if unit_tokens <= max_tokens // 3 else ""
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
            # Chia theo Khoản: không gộp hai Điều vào một chunk (nhãn trích dẫn phải đúng một Điều).
            same_dieu = not self._by_khoan or _dieus(previous) == _dieus(chunk)

            should_merge = same_chapter and same_dieu and total <= cfg.chunk_hard_cap and (
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
                for unit in chunk.units:
                    if unit not in previous.units:
                        previous.units.append(unit)
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
