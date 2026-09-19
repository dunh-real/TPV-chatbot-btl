"""Generate a grounded DeckSpec through Ollama structured output."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    import ollama
except ImportError:
    ollama = None

from pydantic import ValidationError

from src.models.presentation_contracts import DeckSpec
from src.services.presentation.document_summarizer import prepare_source_context
from src.services.presentation.markdown_parser import SourceGraph
from src.services.presentation.planner_prompt import build_planner_prompt
from src.services.presentation.planner_quality import (
    DeckQualityError,
    deck_quality_issues,
    retry_instruction,
)

logger = logging.getLogger(__name__)
DEFAULT_MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen3:latest")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def generate_deck_spec(
    source_graph: SourceGraph,
    options: dict[str, Any] | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    max_retries: int = 2,
) -> DeckSpec:
    if ollama is None:
        raise RuntimeError("Ollama package is not installed.")

    opts = options or {}
    client = ollama.Client(host=OLLAMA_BASE_URL)
    context = prepare_source_context(source_graph, client, model_name, opts)
    messages = [
        {
            "role": "system",
            "content": (
                "Bạn lập presentation có grounding, cấu trúc trực quan và dễ đọc. "
                "Tuân thủ JSON Schema và không bịa dữ liệu."
            ),
        },
        {"role": "user", "content": build_planner_prompt(source_graph, opts, context)},
    ]
    llm_options = {
        "temperature": float(opts.get("temperature", 0.1)),
        "num_ctx": int(opts.get("num_ctx", 16384)),
        "num_predict": int(opts.get("num_predict", 3000)),
    }
    last_error: Exception | None = None
    raw = ""

    for attempt in range(max_retries + 1):
        try:
            logger.info(
                "Generating DeckSpec with '%s' (attempt %d/%d).",
                model_name, attempt + 1, max_retries + 1,
            )
            response = client.chat(
                model=model_name,
                messages=messages,
                format=DeckSpec.model_json_schema(),
                options=llm_options,
            )
            raw = (response.get("message") or {}).get("content", "")
            if not raw.strip():
                raise ValueError("Ollama returned an empty response.")
            deck = DeckSpec.model_validate_json(raw)
            issues = deck_quality_issues(deck, opts)
            if issues:
                raise DeckQualityError(issues)
            logger.info("Generated '%s' with %d slides.", deck.deck_title, len(deck.slides))
            return deck
        except (DeckQualityError, ValidationError, json.JSONDecodeError, ValueError) as error:
            last_error = error
            logger.warning("Planner attempt %d failed: %s", attempt + 1, error)
            if attempt < max_retries:
                if raw:
                    messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": retry_instruction(error)})
        except Exception as error:
            last_error = error
            logger.exception("Planner attempt %d hit a runtime error.", attempt + 1)
            if attempt < max_retries:
                messages.append({
                    "role": "user",
                    "content": "Lần gọi trước lỗi runtime. Hãy thử lại và chỉ trả về JSON hợp lệ.",
                })

    raise RuntimeError(
        f"Failed to generate a presentation-ready DeckSpec after {max_retries + 1} attempts. "
        f"Last error: {last_error}"
    )
