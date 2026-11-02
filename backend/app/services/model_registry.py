"""Thử model độc lập; local chỉ dùng model đã cấu hình, không tự tải model theo đầu vào."""
import asyncio
import time
import anyio
import httpx

from app.core.config import get_settings
from app.services.agent_config import validate_endpoint
from app.services.integration_errors import IntegrationError

MODEL_TEST_SECONDS = 30
_local_job = None


async def local_call(function):
    """Timeout không dừng được CUDA kernel; giữ job tới khi xong và từ chối test chồng."""
    global _local_job
    if _local_job is not None and not _local_job.done():
        raise IntegrationError("OVERLOADED", "Model local đang chạy lượt test khác", 503)
    _local_job = asyncio.create_task(anyio.to_thread.run_sync(function))
    return await asyncio.shield(_local_job)


async def test_model(request):
    started = time.monotonic()
    cfg, model = get_settings(), request.model
    if model.endpointUrl is None:
        local_types = {cfg.llm_model: "llm", cfg.embedding_model: "embedding", cfg.reranker_model: "reranker"}
        if model.code in local_types:
            model = model.model_copy(update={"type": local_types[model.code]})
    async with asyncio.timeout(MODEL_TEST_SECONDS):
        if model.endpointUrl:
            endpoint = validate_endpoint(model.endpointUrl)
            route = {"llm": "/chat/completions", "embedding": "/embeddings", "reranker": "/rerank"}[model.type]
            data = {"model": model.code}
            if model.type == "llm":
                data.update(messages=[{"role": "user", "content": request.input}], max_tokens=32, stream=False)
            elif model.type == "embedding":
                data["input"] = [request.input]
            else:
                data.update(query=request.input, documents=request.documents)
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.post(endpoint + route, json=data,
                    headers={"Authorization": "Bearer " + cfg.llm_api_key})
                response.raise_for_status()
                result = response.json()
            if model.type == "llm":
                sample = result["choices"][0]["message"].get("content", "")
            elif model.type == "embedding":
                sample = {"dimensions": len(result["data"][0]["embedding"])}
            else:
                sample = result.get("results", [])[:3]
        elif model.type == "embedding" and model.code == cfg.embedding_model:
            from app.rag.embedding import get_embedder
            dense, _ = await local_call(lambda: get_embedder().encode([request.input]))
            sample = {"dimensions": len(dense[0])}
        elif model.type == "reranker" and model.code == cfg.reranker_model:
            from app.rag.reranker import get_reranker
            scores = await local_call(lambda: get_reranker().score(request.input, request.documents))
            sample = {"scores": [float(v) for v in scores]}
        elif model.type == "llm" and model.code == cfg.llm_model:
            nested = request.model_copy(update={"model": model.model_copy(update={"endpointUrl": cfg.llm_base_url})})
            return await test_model(nested)
        else:
            raise IntegrationError("CONFIG_INVALID", "Code model local chưa được đăng ký", 422)
    return {"ok": True, "code": model.code, "type": model.type, "elapsedMs": round((time.monotonic()-started)*1000), "result": sample}
