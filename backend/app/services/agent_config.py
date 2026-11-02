"""Cấu hình Agent lưu JSON nguyên tử; mỗi request đọc một bản, dùng chung giữa worker."""
import hashlib
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.core import runtime
from app.schemas.admin import AgentConfig
from app.services.integration_errors import IntegrationError

AGENTS = {"qa", "agent", "review", "summarize", "draft", "aggregate", "slide", "routing", "mindmap", "ocr"}


def require_agent(code):
    if code not in AGENTS:
        raise IntegrationError("AGENT_NOT_FOUND", "Mã Agent không tồn tại", 404)


def validate_endpoint(endpoint):
    url = urlsplit(endpoint or "")
    cfg = get_settings()
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.hostname not in cfg.admin_model_hosts):
        raise IntegrationError("CONFIG_INVALID", "Endpoint model không thuộc danh sách host được phép", 422)
    return endpoint.rstrip("/")


def load(code):
    require_agent(code)
    path = Path(get_settings().agent_config_dir) / (code + ".json")
    try:
        return AgentConfig.model_validate_json(path.read_text()) if path.exists() else None
    except (ValueError, OSError) as exc:
        raise IntegrationError("CONFIG_INVALID", "Không đọc được cấu hình Agent đã lưu", 503) from exc


def save(code, config):
    require_agent(code)
    if config.model.endpointUrl:
        validate_endpoint(config.model.endpointUrl)
    elif config.model.code != get_settings().llm_model:
        raise IntegrationError("CONFIG_INVALID", "Model LLM local chưa được đăng ký", 422)
    folder = Path(get_settings().agent_config_dir)
    folder.mkdir(parents=True, exist_ok=True)
    body = config.model_dump_json()
    fd, name = tempfile.mkstemp(dir=folder, prefix=".agent-")
    try:
        with os.fdopen(fd, "w") as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, folder / (code + ".json"))
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


@asynccontextmanager
async def activate(config):
    if config is None:
        yield
        return
    cfg = get_settings()
    model, params = config.model, config.parameters
    if model.endpointUrl:
        endpoint = validate_endpoint(model.endpointUrl)
    else:
        if model.code != cfg.llm_model:
            raise IntegrationError("CONFIG_INVALID", "Model LLM local chưa được đăng ký", 422)
        endpoint = cfg.llm_base_url
    updated = cfg.model_copy(update={"llm_model": model.code, "llm_utility_model": "",
        "llm_base_url": endpoint, "llm_temperature": params.temperature,
        "llm_max_tokens": params.maxAnswerTokens, "llm_enable_thinking": params.enableThinking,
        "rerank_top_n": params.topK, "erp_text2sql_base_url": endpoint,
        "erp_text2sql_model": model.code, "erp_text2sql_api_key": cfg.llm_api_key,
        "ocr_base_url": endpoint, "ocr_model": model.code,
        "erp_text2sql_max_tokens": params.maxAnswerTokens})
    from app.services.llm import LLMClient
    client = LLMClient(updated)
    tokens = [(runtime.agent_config, runtime.agent_config.set(config)),
              (runtime.runtime_settings, runtime.runtime_settings.set(updated)),
              (runtime.runtime_llm, runtime.runtime_llm.set(client))]
    try:
        yield
    finally:
        await client.close()
        for var, token in reversed(tokens):
            var.reset(token)
