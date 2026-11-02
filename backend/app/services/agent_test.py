"""Chạy thử workflow trong ngữ cảnh cô lập; không lưu cấu hình hoặc lịch sử."""
import asyncio
import tempfile
from pathlib import Path

from app.core import runtime
from app.core.config import get_settings
from app.services.agent_config import activate
from app.services.integration_errors import IntegrationError

AGENT_TEST_SECONDS = 120


async def run(code, request):
    from app.agents import graph, quyen
    from app.services import storage
    permission = {"qa": "Ai.AiChatbot", "agent": "Ai.AiChatbot", "review": "Ai.DocumentReview",
        "summarize": "Ai.Summarize", "draft": "Ai.ReportDrafting", "aggregate": "Ai.ReportSummary.Create",
        "slide": "Ai.Slide.Create", "routing": "Ai.Routing", "mindmap": "Ai.AiChatbot", "ocr": "Ai.Ocr"}.get(code)
    if permission is None:
        raise IntegrationError("CONFIG_INVALID", "Chưa có bộ chạy test an toàn cho Agent này", 422)
    quyen.can_quyen(permission)()
    token = runtime.test_mode.set(True)
    try:
        async with asyncio.timeout(AGENT_TEST_SECONDS), activate(request.config):
            with tempfile.TemporaryDirectory(prefix="tpv-agent-test-") as folder:
                cfg = get_settings().model_copy(update={"output_dir": folder})
                setting_token = runtime.runtime_settings.set(cfg)
                try:
                    if code == "qa":
                        state = await graph.build_initial_state(request.request, history=request.history, persist_history=False)
                        result = await graph.run_qa(state)
                    elif code == "agent":
                        result = await graph.run_agent(request.request, file_id=request.file_id)
                    elif code in {"summarize", "review"}:
                        ref = storage.resolve(request.file_id, kinds=("upload",))
                        if code == "summarize":
                            result = await graph.run_summary_workflow(str(ref.path), ref.name)
                        else:
                            result = await graph.run_document_workflow(str(ref.path), ref.name)
                    elif code == "draft":
                        result = await graph.run_draft_workflow(request.request, inputs=request.inputs)
                    elif code == "routing":
                        from app.db.routing_repository import catalogs
                        from app.services.document_routing import suggest, response
                        departments, categories = await catalogs()
                        text = request.request
                        if request.file_id:
                            from app.api.document_routings import read_file
                            text = await read_file(storage.resolve(request.file_id, kinds=("upload",)).path)
                        mapped = await suggest(text, departments, categories)
                        result = response(mapped, document_id="", task_id=None, departments=departments, categories=categories)
                    elif code == "mindmap":
                        import anyio
                        from app.documents.parser import parse_document
                        from app.documents.mindmap import build_batches, generate_skeleton
                        ref = storage.resolve(request.file_id, kinds=("upload",))
                        document = await anyio.to_thread.run_sync(lambda: parse_document(ref.path))
                        texts = [b.text for b in document.blocks if not b.is_empty]
                        result = {"tree": await generate_skeleton(build_batches(texts), ref.name, sum(len(t.split()) for t in texts))}
                    elif code == "ocr":
                        from app.api.ocr import _chay
                        ref = storage.resolve(request.file_id, kinds=("upload",))
                        result = {"pages": []}
                        async for event, payload in _chay(ref.path, ref.name, "auto"):
                            if event == "error":
                                raise IntegrationError("MODEL_ERROR", "Không đọc được file OCR", 502)
                            if event == "trang":
                                result["pages"].append(payload)
                    elif code == "aggregate":
                        result = await graph.run_aggregate_workflow(request.request, inputs=request.inputs)
                    else:
                        result = await graph.run_presentation_workflow(request.request, inputs=request.inputs,
                            nguon="tai_lieu" if request.file_id else "csdl", file_id=request.file_id)
                    if result.get("error"):
                        raise IntegrationError("MODEL_ERROR", str(result["error"]), 502)
                    # File test bị dọn; không công bố link tải không còn tồn tại.
                    result = {k: v for k, v in result.items() if k not in {"output_path", "output_file_url", "download_url", "files"}}
                    return {"test": True, "agentCode": code, "result": result}
                finally:
                    runtime.runtime_settings.reset(setting_token)
    finally:
        runtime.test_mode.reset(token)
