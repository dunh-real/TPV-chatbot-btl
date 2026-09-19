"""Convert a DeckSpec into the small JSON contract consumed by the renderer."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.models.presentation_contracts import DeckSpec

logger = logging.getLogger(__name__)
MAX_BODY_ITEMS = 6
MAX_BODY_CHARS = 500


def _text_items(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = [re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line) for line in lines]
    if len(items) == 1 and items[0].count(";") >= 2:
        items = [part.strip() for part in items[0].split(";") if part.strip()]
    if len(items) == 1 and len(items[0]) > MAX_BODY_CHARS:
        sentences = re.split(r"(?<=[.!?])\s+", items[0])
        items = sentences if len(sentences) > 1 else items
    return items


def _text_chunks(items: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for item in items:
        too_large = (
            len(current) >= MAX_BODY_ITEMS
            or sum(map(len, current)) + len(item) > MAX_BODY_CHARS
        )
        if current and too_large:
            chunks.append(current)
            current = []
        current.append(item)
    if current:
        chunks.append(current)
    return chunks


def _components(slide: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "body_text": [], "chart": None, "table": None, "diagram": None, "image": None
    }
    for block in slide.blocks:
        data = block.model_dump() if hasattr(block, "model_dump") else block
        block_type = data.get("type")
        if block_type == "text":
            result["body_text"].extend(_text_items(data.get("text", "")))
        elif block_type in {"chart", "table", "diagram"}:
            result[block_type] = {
                key: value for key, value in data.items() if key not in {"id", "type"}
            }
    return result


def _render_slide(slide: Any, components: dict[str, Any], slide_id: str) -> dict[str, Any]:
    kind = slide.kind
    for visual_kind in ("diagram", "chart", "table"):
        if components[visual_kind]:
            kind = visual_kind
            break
    body = components["body_text"]
    return {
        "id": slide_id,
        "kind": kind,
        "title": slide.title,
        "subtitle": body[0] if kind == "cover" and body else None,
        "body_text": body[1:] if kind == "cover" and body else body,
        "image": components["image"],
        "chart": components["chart"],
        "table": components["table"],
        "diagram": components["diagram"],
        "speaker_notes": slide.speaker_notes,
    }


def compose_render_plan(deck_spec: DeckSpec) -> dict[str, Any]:
    slides: list[dict[str, Any]] = []
    for slide in deck_spec.slides:
        components = _components(slide)
        body = components["body_text"]
        total_chars = sum(map(len, body))
        should_split = slide.kind in {"text", "image_text"} and (
            len(body) > MAX_BODY_ITEMS or total_chars > MAX_BODY_CHARS
        )
        chunks = _text_chunks(body)
        if not should_split or not chunks:
            chunks = [body]

        for index, chunk in enumerate(chunks):
            current = dict(components)
            current["body_text"] = chunk
            if index:
                current.update({"chart": None, "table": None, "diagram": None, "image": None})
            item = _render_slide(slide, current, f"s{len(slides) + 1:02d}")
            if len(chunks) > 1:
                item["title"] = f"{slide.title} (Phần {index + 1})"
                item["speaker_notes"] = slide.speaker_notes if index == 0 else ""
            slides.append(item)

    logger.info("Composed RenderPlan '%s' with %d slides.", deck_spec.deck_title, len(slides))
    return {
        "schema_version": "2.0",
        "deck_title": deck_spec.deck_title,
        "language": deck_spec.language,
        "total_slides": len(slides),
        "slides": slides,
    }
