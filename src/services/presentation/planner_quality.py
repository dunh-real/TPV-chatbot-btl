"""Small quality gate for schema-valid presentation plans."""

from __future__ import annotations

import re
from typing import Any

from src.models.presentation_contracts import DeckSpec
from src.services.presentation.planner_prompt import slide_range


class DeckQualityError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def deck_quality_issues(deck: DeckSpec, options: dict[str, Any]) -> list[str]:
    slides = list(deck.slides)
    minimum, maximum = slide_range(options)
    issues: list[str] = []
    if not slides:
        return ["Deck không có slide."]
    if not minimum <= len(slides) <= maximum:
        issues.append(f"Số slide {len(slides)} nằm ngoài khoảng {minimum}–{maximum}.")
    if slides[0].kind != "cover":
        issues.append("Slide đầu tiên phải có kind='cover'.")

    if not options.get("allow_section_dividers", False):
        extra = [slide.id for slide in slides[1:] if slide.kind == "cover"]
        if extra:
            issues.append(f"Có cover ngoài slide đầu: {', '.join(extra)}.")

    visual_kinds = {"diagram", "chart", "table", "image_text"}
    min_visuals = int(options.get("min_visual_slides", 1 if len(slides) >= 6 else 0))
    visual_count = sum(slide.kind in visual_kinds for slide in slides)
    if visual_count < min_visuals:
        issues.append(f"Cần ít nhất {min_visuals} slide trực quan; hiện có {visual_count}.")

    max_block = int(options.get("max_text_chars_per_block", 650))
    max_slide = int(options.get("max_text_chars_per_slide", 900))
    max_blocks = int(options.get("max_non_cover_blocks", 4))
    previous_title = ""
    for slide in slides:
        if previous_title and slide.title.casefold() == previous_title.casefold():
            issues.append(f"{slide.id} lặp title của slide ngay trước: '{slide.title}'.")
        previous_title = slide.title
        texts = [block.text for block in slide.blocks if block.type == "text"]
        if slide.kind != "cover" and len(slide.blocks) > max_blocks:
            issues.append(f"{slide.id} có quá {max_blocks} blocks.")
        if slide.kind != "cover" and sum(map(len, texts)) > max_slide:
            issues.append(f"{slide.id} vượt text budget {max_slide} ký tự.")
        for block, text in zip((b for b in slide.blocks if b.type == "text"), texts):
            if len(text) > max_block:
                issues.append(f"TextBlock {block.id} trong {slide.id} vượt {max_block} ký tự.")
            if len(re.findall(r"(?m)^\s*1\.\s+", text)) >= 2:
                issues.append(f"{slide.id} có numbered list lặp '1.'.")
    return issues


def retry_instruction(error: Exception) -> str:
    if isinstance(error, DeckQualityError):
        details = "\n".join(f"- {issue}" for issue in error.issues)
        return f"""DeckSpec chưa đạt chất lượng trình bày:
{details}

Hãy lập lại deck, sửa đúng các lỗi trên và chỉ trả về JSON hợp lệ."""
    return f"JSON không hợp lệ hoặc sai schema: {error}. Hãy sửa và chỉ trả về JSON hợp lệ."
