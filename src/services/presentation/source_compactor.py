from __future__ import annotations

import json
import unicodedata
from typing import Any

from src.services.presentation.markdown_parser import SourceGraph


def estimate_tokens(value: Any) -> int:
    """Conservative token estimate for Vietnamese and JSON."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, (len(text) + 2) // 3)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _is_toc(path: list[str]) -> bool:
    names = {"muc luc", "table of contents", "contents"}
    return any(_plain(part).strip(" #:.-") in names for part in path)


def _compact_block(block: Any) -> dict[str, Any]:
    kind = block.kind
    metadata = block.metadata or {}
    result: dict[str, Any] = {"id": block.id, "kind": kind}

    if kind == "list":
        result["items"] = [line for line in block.text.splitlines() if line.strip()]
    elif kind == "table":
        result["headers"] = metadata.get("headers", [])
        result["rows"] = metadata.get("rows", [])
    elif kind == "image":
        result["alt"] = metadata.get("alt", block.text)
        result["url"] = metadata.get("url", "")
    elif kind == "code":
        result["language"] = metadata.get("language", "")
        result["code"] = block.text[:2400]
    else:
        result["text"] = block.text

    return result


def compact_source_graph(source_graph: SourceGraph) -> dict[str, Any]:
    """Remove repeated structure while preserving evidence and source IDs."""
    sections: list[dict[str, Any]] = []

    for block in source_graph.blocks:
        path = list(block.section_path or [])
        if block.kind == "heading" or _is_toc(path):
            continue

        if not sections or sections[-1]["path"] != path:
            sections.append({"path": path, "blocks": []})
        sections[-1]["blocks"].append(_compact_block(block))

    return {
        "document_id": source_graph.document_id,
        "title": source_graph.title,
        "sections": [section for section in sections if section["blocks"]],
    }


def compact_source_json(source_graph: SourceGraph) -> str:
    return json.dumps(
        compact_source_graph(source_graph),
        ensure_ascii=False,
        separators=(",", ":"),
    )
