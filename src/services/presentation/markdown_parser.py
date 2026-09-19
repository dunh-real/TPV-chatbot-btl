"""
Markdown Parser Service for Presentation Generation.
Converts raw Markdown text into a structured SourceGraph with typed SourceBlock objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from markdown_it import MarkdownIt


_DIRECTIVE_RE = re.compile(r"<!--\s*(slide:[\s\S]*?)\s*-->", re.IGNORECASE)


@dataclass
class SourceBlock:
    """Represents a single structural element extracted from Markdown."""

    id: str
    kind: str  # heading, paragraph, list, table, image, code, directive
    text: str
    section_path: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "section_path": self.section_path,
            "metadata": self.metadata,
        }


@dataclass
class SourceGraph:
    """Represents the full structured graph of a Markdown document."""

    document_id: str
    title: str
    blocks: List[SourceBlock] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "blocks": [b.to_dict() for b in self.blocks],
        }


def _extract_inline_text(token: Any) -> str:
    """Extract readable inline text while intentionally excluding images."""
    if token is None:
        return ""
    if not hasattr(token, "children") or not token.children:
        return token.content.strip() if hasattr(token, "content") else ""

    text_parts: List[str] = []
    for child in token.children:
        if child.type in ("text", "code_inline"):
            text_parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            text_parts.append("\n")
        elif child.type == "image":
            # Image is emitted as its own SourceBlock.
            continue
        elif hasattr(child, "content") and child.content:
            text_parts.append(child.content)

    return "".join(text_parts).strip()


def _clean_directives_from_text(text: str) -> str:
    """Remove presentation directives from prose while preserving surrounding text."""
    cleaned = _DIRECTIVE_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _find_matching_list_close(tokens: Sequence[Any], start_idx: int) -> int:
    """Return the matching close token for a list open token, including nested lists."""
    open_type = tokens[start_idx].type
    close_type = open_type.replace("_open", "_close")
    depth = 0

    for idx in range(start_idx, len(tokens)):
        token_type = tokens[idx].type
        if token_type == open_type:
            depth += 1
        elif token_type == close_type:
            depth -= 1
            if depth == 0:
                return idx

    return len(tokens) - 1


def _direct_list_item_ranges(tokens: Sequence[Any], list_start: int, list_end: int) -> List[Tuple[int, int]]:
    """Get direct child list-item ranges without confusing nested list items."""
    list_level = tokens[list_start].level
    item_level = list_level + 1
    ranges: List[Tuple[int, int]] = []
    item_start: int | None = None

    for idx in range(list_start + 1, list_end):
        tok = tokens[idx]
        if tok.type == "list_item_open" and tok.level == item_level:
            item_start = idx
        elif tok.type == "list_item_close" and tok.level == item_level and item_start is not None:
            ranges.append((item_start, idx))
            item_start = None

    return ranges


def _parse_list_items(tokens: Sequence[Any], list_start: int, list_end: int) -> List[Dict[str, Any]]:
    """Parse direct items and nested child lists into a structured representation."""
    result: List[Dict[str, Any]] = []
    parent_level = tokens[list_start].level

    for item_start, item_end in _direct_list_item_ranges(tokens, list_start, list_end):
        direct_text_parts: List[str] = []
        children: List[Dict[str, Any]] = []
        idx = item_start + 1

        while idx < item_end:
            tok = tokens[idx]

            # A nested list opens two levels below the parent list.
            if tok.type in ("bullet_list_open", "ordered_list_open") and tok.level == parent_level + 2:
                nested_end = _find_matching_list_close(tokens, idx)
                nested_ordered = tok.type == "ordered_list_open"
                nested_start = int((tok.attrs or {}).get("start", 1)) if nested_ordered else None
                children.append(
                    {
                        "ordered": nested_ordered,
                        "start": nested_start,
                        "items": _parse_list_items(tokens, idx, nested_end),
                    }
                )
                idx = nested_end + 1
                continue

            # Inline text that belongs directly to this list item (not a nested item).
            if tok.type == "inline" and tok.level == parent_level + 3:
                value = _clean_directives_from_text(_extract_inline_text(tok))
                if value:
                    direct_text_parts.append(value)

            idx += 1

        result.append(
            {
                "text": " ".join(direct_text_parts).strip(),
                "children": children,
            }
        )

    return result


def _flatten_list_text(
    items: List[Dict[str, Any]],
    *,
    ordered: bool,
    start: int = 1,
    indent: int = 0,
) -> List[str]:
    """Create a readable text fallback while retaining correct numbering."""
    lines: List[str] = []

    for offset, item in enumerate(items):
        prefix = f"{start + offset}." if ordered else "-"
        item_text = item.get("text", "").strip()
        if item_text:
            lines.append(f"{'  ' * indent}{prefix} {item_text}")

        for child_list in item.get("children", []):
            child_ordered = bool(child_list.get("ordered"))
            child_start = int(child_list.get("start") or 1)
            lines.extend(
                _flatten_list_text(
                    child_list.get("items", []),
                    ordered=child_ordered,
                    start=child_start,
                    indent=indent + 1,
                )
            )

    return lines


def _build_markdown_parser() -> MarkdownIt:
    """Create the parser in one place so extensions can be added consistently."""
    return MarkdownIt("js-default")


def parse_markdown(md_text: str, document_id: str = "doc-001") -> SourceGraph:
    """
    Parse Markdown into an ordered SourceGraph.

    Important presentation-oriented behavior:
    - heading hierarchy is preserved in ``section_path``;
    - list items remain structured in metadata (including nested lists);
    - ordered list numbering is preserved;
    - text is not discarded when a paragraph also contains an image;
    - ``<!-- slide:... -->`` directives are emitted separately from prose.
    """
    if not md_text or not md_text.strip():
        return SourceGraph(document_id=document_id, title="Untitled Presentation", blocks=[])

    md = _build_markdown_parser()
    tokens = md.parse(md_text)

    section_by_level: Dict[int, str] = {}
    blocks: List[SourceBlock] = []
    block_counter = 1
    doc_title = ""

    def next_id() -> str:
        nonlocal block_counter
        value = f"src-{block_counter:04d}"
        block_counter += 1
        return value

    def current_section_path() -> List[str]:
        return [section_by_level[level] for level in sorted(section_by_level)]

    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # 1. Headings
        if tok.type == "heading_open":
            level = int(tok.tag[1]) if len(tok.tag) > 1 and tok.tag[1].isdigit() else 1
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            title = _extract_inline_text(inline_tok) if inline_tok else ""

            if level == 1 and not doc_title:
                doc_title = title

            # Replace this level and discard any deeper stale headings.
            for existing_level in list(section_by_level):
                if existing_level >= level:
                    del section_by_level[existing_level]
            if title:
                section_by_level[level] = title

            blocks.append(
                SourceBlock(
                    id=next_id(),
                    kind="heading",
                    text=title,
                    section_path=current_section_path(),
                    metadata={
                        "level": level,
                        "is_document_title": level == 1 and title == doc_title,
                    },
                )
            )
            i += 3
            continue

        # 2. Paragraphs, directives and images
        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
            if inline_tok is not None:
                raw_inline = inline_tok.content.strip()
                directives = [m.group(1).strip() for m in _DIRECTIVE_RE.finditer(raw_inline)]

                text = _clean_directives_from_text(_extract_inline_text(inline_tok))
                if text:
                    blocks.append(
                        SourceBlock(
                            id=next_id(),
                            kind="paragraph",
                            text=text,
                            section_path=current_section_path(),
                            metadata={},
                        )
                    )

                # Preserve every inline image even if the same paragraph also has prose.
                if getattr(inline_tok, "children", None):
                    for child in inline_tok.children:
                        if child.type != "image":
                            continue
                        attrs = child.attrs or {}
                        url = attrs.get("src", "")
                        alt = child.content or attrs.get("alt", "")
                        blocks.append(
                            SourceBlock(
                                id=next_id(),
                                kind="image",
                                text=alt or url,
                                section_path=current_section_path(),
                                metadata={
                                    "url": url,
                                    "alt": alt,
                                    "title": attrs.get("title", ""),
                                },
                            )
                        )

                for directive in directives:
                    blocks.append(
                        SourceBlock(
                            id=next_id(),
                            kind="directive",
                            text=directive,
                            section_path=current_section_path(),
                            metadata={
                                "raw": f"<!-- {directive} -->",
                                "directive": directive,
                            },
                        )
                    )

            i += 3
            continue

        # 3. Lists. Only parse root list tokens here; nested lists are consumed recursively.
        if tok.type in ("bullet_list_open", "ordered_list_open"):
            list_end = _find_matching_list_close(tokens, i)
            ordered = tok.type == "ordered_list_open"
            start = int((tok.attrs or {}).get("start", 1)) if ordered else 1
            structured_items = _parse_list_items(tokens, i, list_end)
            item_texts = [item.get("text", "") for item in structured_items if item.get("text", "")]
            lines = _flatten_list_text(structured_items, ordered=ordered, start=start)

            if structured_items:
                blocks.append(
                    SourceBlock(
                        id=next_id(),
                        kind="list",
                        text="\n".join(lines),
                        section_path=current_section_path(),
                        metadata={
                            # Backwards-compatible flat list for existing consumers.
                            "items": item_texts,
                            "ordered": ordered,
                            "start": start if ordered else None,
                            # Rich representation for the content planner / future renderer.
                            "structured_items": structured_items,
                        },
                    )
                )

            i = list_end + 1
            continue

        # 4. Tables
        if tok.type == "table_open":
            headers: List[str] = []
            rows: List[List[str]] = []
            current_row: List[str] = []
            in_thead = False

            i += 1
            while i < len(tokens) and tokens[i].type != "table_close":
                table_tok = tokens[i]
                if table_tok.type == "thead_open":
                    in_thead = True
                elif table_tok.type == "thead_close":
                    in_thead = False
                elif table_tok.type == "tr_close":
                    if in_thead:
                        headers = list(current_row)
                    elif current_row:
                        rows.append(list(current_row))
                    current_row = []
                elif table_tok.type == "inline":
                    current_row.append(_extract_inline_text(table_tok))
                i += 1

            summary_text = f"Table with headers: {', '.join(headers)}" if headers else "Table"
            blocks.append(
                SourceBlock(
                    id=next_id(),
                    kind="table",
                    text=summary_text,
                    section_path=current_section_path(),
                    metadata={"headers": headers, "rows": rows},
                )
            )
            i += 1
            continue

        # 5. Code blocks
        if tok.type in ("fence", "code_block"):
            code_text = tok.content.strip()
            lang = tok.info.strip() if getattr(tok, "info", "") else ""
            blocks.append(
                SourceBlock(
                    id=next_id(),
                    kind="code",
                    text=code_text,
                    section_path=current_section_path(),
                    metadata={"language": lang, "code": code_text},
                )
            )
            i += 1
            continue

        i += 1

    # Fallback title if no H1 heading was found.
    if not doc_title:
        for block in blocks:
            if block.kind == "heading":
                doc_title = block.text
                break
        if not doc_title:
            doc_title = "Untitled Presentation"

    return SourceGraph(document_id=document_id, title=doc_title, blocks=blocks)
