from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.services.presentation.markdown_parser import SourceGraph
from src.services.presentation.source_compactor import (
    compact_source_graph,
    estimate_tokens,
)

logger = logging.getLogger(__name__)


class ChunkDigest(BaseModel):
    title: str = Field(max_length=120)
    summary: str = Field(max_length=600)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    facts_and_numbers: list[str] = Field(default_factory=list, max_length=8)
    visual_candidates: list[str] = Field(default_factory=list, max_length=4)
    source_block_ids: list[str] = Field(default_factory=list, max_length=12)


def _split_sections(payload: dict[str, Any], max_tokens: int) -> list[dict[str, Any]]:
    base = {"document_id": payload["document_id"], "title": payload["title"]}
    chunks: list[dict[str, Any]] = []
    current = {**base, "sections": []}

    sections: list[dict[str, Any]] = []
    for section in payload["sections"]:
        part = {"path": section["path"], "blocks": []}
        for block in section["blocks"]:
            candidate = {"path": section["path"], "blocks": [*part["blocks"], block]}
            if part["blocks"] and estimate_tokens(candidate) > max_tokens:
                sections.append(part)
                part = {"path": section["path"], "blocks": [block]}
            else:
                part = candidate
        if part["blocks"]:
            sections.append(part)

    for section in sections:
        candidate = {**base, "sections": [*current["sections"], section]}
        if current["sections"] and estimate_tokens(candidate) > max_tokens:
            chunks.append(current)
            current = {**base, "sections": [section]}
        else:
            current = candidate

    if current["sections"]:
        chunks.append(current)
    return chunks


def _cache_file(cache_dir: Path, model: str, payload: dict[str, Any]) -> Path:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = sha256(f"v1:{model}:{raw}".encode("utf-8")).hexdigest()
    return cache_dir / f"{key}.json"


def _summarize_chunk(
    client: Any,
    model: str,
    payload: dict[str, Any],
    cache_dir: Path,
    num_predict: int,
) -> ChunkDigest:
    cache_path = _cache_file(cache_dir, model, payload)
    if cache_path.exists():
        try:
            return ChunkDigest.model_validate_json(cache_path.read_text(encoding="utf-8"))
        except ValueError:
            logger.warning("Ignoring an outdated summary cache entry.")

    prompt = f"""Tóm tắt phần tài liệu dưới đây để lập slide.
- Chỉ dùng thông tin trong nguồn, không suy diễn.
- Giữ chính xác số liệu, tên riêng, quyết định và thứ tự quy trình.
- summary tối đa 600 ký tự; key_points tối đa 6 mục.
- source_block_ids chỉ giữ tối đa 12 ID quan trọng nhất có trong nguồn.

Nguồn:
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"""

    messages = [
        {"role": "system", "content": "Bạn tóm tắt tài liệu có kiểm chứng cho presentation planner."},
        {"role": "user", "content": prompt},
    ]
    budgets = [num_predict, max(1200, num_predict + 350)]
    for attempt, budget in enumerate(budgets):
        response = client.chat(
            model=model,
            messages=messages,
            format=ChunkDigest.model_json_schema(),
            options={"temperature": 0.0, "num_predict": budget},
        )
        raw = (response.get("message") or {}).get("content", "")
        try:
            digest = ChunkDigest.model_validate_json(raw)
            break
        except ValueError:
            if attempt == len(budgets) - 1:
                raise
            logger.warning("Summary JSON was incomplete; retrying with %d output tokens.", budgets[1])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(digest.model_dump_json(indent=2), encoding="utf-8")
    return digest


def _summarize_many(
    client: Any,
    model: str,
    payloads: list[dict[str, Any]],
    cache_dir: Path,
    num_predict: int,
    workers: int,
) -> list[ChunkDigest]:
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(payloads)))) as pool:
        return list(
            pool.map(
                lambda item: _summarize_chunk(client, model, item, cache_dir, num_predict),
                payloads,
            )
        )


def _digest_payload(source_graph: SourceGraph, digests: list[ChunkDigest]) -> dict[str, Any]:
    sections = []
    for digest in digests:
        sections.append(
            {
                "path": [digest.title],
                "blocks": [{"kind": "digest", **digest.model_dump()}],
            }
        )
    return {
        "document_id": source_graph.document_id,
        "title": source_graph.title,
        "sections": sections,
    }


def prepare_source_context(
    source_graph: SourceGraph,
    client: Any,
    model: str,
    options: dict[str, Any],
) -> str:
    """Return compact source JSON; summarize only when it exceeds the budget."""
    payload = compact_source_graph(source_graph)
    source_budget = int(options.get("source_token_budget", 3600))
    if estimate_tokens(payload) <= source_budget:
        logger.info("Source fits planner budget; skipping summarization.")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    chunk_tokens = int(options.get("summary_chunk_tokens", 4200))
    chunks = _split_sections(payload, chunk_tokens)
    cache_dir = Path(options.get("summary_cache_dir", ".cache/presentation_summaries"))
    num_predict = int(options.get("summary_num_predict", 850))
    workers = int(options.get("summary_concurrency", 2))
    logger.info("Summarizing %d source chunks with %d workers.", len(chunks), workers)
    digests = _summarize_many(client, model, chunks, cache_dir, num_predict, workers)

    for _ in range(int(options.get("summary_reduce_rounds", 2))):
        reduced_payload = _digest_payload(source_graph, digests)
        if estimate_tokens(reduced_payload) <= source_budget:
            break
        chunks = _split_sections(reduced_payload, chunk_tokens)
        digests = _summarize_many(
            client, model, chunks, cache_dir, num_predict, workers
        )

    result = {
        "document_id": source_graph.document_id,
        "title": source_graph.title,
        "summarized": True,
        "digests": [digest.model_dump() for digest in digests],
    }
    logger.info(
        "Source reduced from ~%d to ~%d tokens.",
        estimate_tokens(payload),
        estimate_tokens(result),
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
