"""Chọn model chat theo từng lượt; giữ quyền, prompt và cấu hình nghiệp vụ hiện có."""
from contextlib import asynccontextmanager

from app.core import runtime
from app.core.config import get_settings
from app.schemas.admin import AgentConfig, ModelConfig
from app.services.integration_errors import IntegrationError


def model_choices():
    cfg = get_settings()
    return [dict(id=cfg.llm_model, name="Qwen3.6-35B · Đầy đủ", context_length=cfg.chat_main_context),
            dict(id=cfg.chat_small_model, name="Qwen3.5-4B · Nhẹ", context_length=cfg.chat_small_context)]


@asynccontextmanager
async def selected_model(code):
    if not code:
        yield
        return
    cfg = get_settings()
    # Chỉ dùng endpoint do quản trị cấu hình; không nhận URL từ người dùng.
    from app.core.config import _base_settings
    base = _base_settings()
    choices = {base.llm_model: (base.llm_base_url, base.llm_api_key, base.chat_main_context),
               base.chat_small_model: (base.chat_small_base_url, base.chat_small_api_key, base.chat_small_context)}
    if code not in choices:
        raise IntegrationError("INVALID_REQUEST", "Model không nằm trong danh sách được phép", 422)
    endpoint, key, context = choices[code]
    active = runtime.agent_config.get()
    config = active.model_copy(deep=True) if active else AgentConfig(model=ModelConfig(code=code))
    config.model = ModelConfig(code=code, endpointUrl=endpoint, maxContextLength=context)
    config.parameters.maxAnswerTokens = min(config.parameters.maxAnswerTokens, 2048, context // 4)
    config.parameters.enableThinking = False
    updated = cfg.model_copy(update={"llm_model": code, "llm_utility_model": "",
        "llm_base_url": endpoint, "llm_api_key": key, "llm_enable_thinking": False,
        "llm_max_tokens": config.parameters.maxAnswerTokens})
    from app.services.llm import LLMClient
    client = LLMClient(updated)
    tokens = [(runtime.agent_config, runtime.agent_config.set(config)),
              (runtime.runtime_settings, runtime.runtime_settings.set(updated)),
              (runtime.runtime_llm, runtime.runtime_llm.set(client))]
    try:
        yield
    finally:
        await client.close()
        for variable, token in reversed(tokens):
            variable.reset(token)
