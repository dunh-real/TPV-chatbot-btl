"""Markdown-to-PowerPoint API."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from src.services.presentation.composer import compose_render_plan
from src.services.presentation.content_planner import DEFAULT_MODEL_NAME, generate_deck_spec
from src.services.presentation.markdown_parser import parse_markdown
from src.services.presentation.renderer_client import render_pptx_from_plan

router = APIRouter(prefix="/presentations", tags=["Presentations"])
logger = logging.getLogger(__name__)
MAX_MARKDOWN_BYTES = 5 * 1024 * 1024


def _build_presentation(markdown: str, source_name: str, options: dict, model: str):
    work_dir = Path(tempfile.mkdtemp(prefix="tpv-slides-"))
    try:
        graph = parse_markdown(markdown, document_id=str(uuid4()))
        deck = generate_deck_spec(graph, options=options, model_name=model)
        plan = compose_render_plan(deck)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(source_name).stem).strip("-")
        output = work_dir / f"{safe_name or 'presentation'}.pptx"
        render_pptx_from_plan(plan, str(output))
        return output, len(plan["slides"]), work_dir
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


@router.post(
    "/generate",
    response_class=FileResponse,
    summary="Tạo PowerPoint từ Markdown",
)
async def generate_presentation(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="File .md, .markdown hoặc .txt"),
    audience: str = Form("người nghe chung"),
    purpose: str = Form("thuyết trình nội dung tài liệu"),
    language: str = Form("vi"),
    slide_min: int = Form(5, ge=2, le=30),
    slide_max: int = Form(8, ge=2, le=30),
    min_visual_slides: int = Form(1, ge=0, le=20),
    model_name: str = Form(DEFAULT_MODEL_NAME),
):
    filename = file.filename or "document.md"
    if Path(filename).suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise HTTPException(400, "Chỉ chấp nhận file Markdown hoặc text.")
    if slide_min > slide_max:
        raise HTTPException(422, "slide_min không được lớn hơn slide_max.")

    content = await file.read()
    await file.close()
    if not content:
        raise HTTPException(400, "File không được rỗng.")
    if len(content) > MAX_MARKDOWN_BYTES:
        raise HTTPException(413, "File vượt quá 5 MB.")
    try:
        markdown = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(400, "File phải dùng UTF-8.") from error

    options = {
        "slide_count": {"min": slide_min, "max": slide_max},
        "audience": audience,
        "purpose": purpose,
        "language": language,
        "min_visual_slides": min_visual_slides,
    }
    try:
        output, slide_count, work_dir = await run_in_threadpool(
            _build_presentation, markdown, filename, options, model_name
        )
    except Exception as error:
        logger.exception("Presentation generation failed.")
        raise HTTPException(500, "Không thể tạo presentation. Xem server log để biết chi tiết.") from error

    background_tasks.add_task(shutil.rmtree, work_dir, True)
    return FileResponse(
        output,
        filename=output.name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"X-Slide-Count": str(slide_count)},
        background=background_tasks,
    )
