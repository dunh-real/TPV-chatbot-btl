"""Kiểm tra chọn model theo request, cách ly quyền/cấu hình và endpoint nghiệp vụ."""
import asyncio
import pytest
from app.core import runtime
from app.core.config import get_settings, _base_settings
from app.schemas.admin import AgentConfig, ModelConfig
from app.services.chat_models import selected_model, model_choices
from app.services.integration_errors import IntegrationError
from app.services.llm import get_llm


@pytest.mark.asyncio
async def test_selected_model_keeps_permissions_and_business_endpoints():
    cfg = get_settings()
    active = AgentConfig(model=ModelConfig(code=cfg.llm_model), systemPrompt='Giữ prompt', allowedPartitionIds=[94])
    token = runtime.agent_config.set(active)
    try:
        async with selected_model(cfg.chat_small_model):
            assert get_llm().settings.llm_model == cfg.chat_small_model
            assert get_settings().erp_text2sql_base_url == cfg.erp_text2sql_base_url
            assert get_settings().ocr_model == cfg.ocr_model
            assert runtime.agent_config.get().allowedPartitionIds == [94]
            assert runtime.agent_config.get().systemPrompt == 'Giữ prompt'
            assert runtime.agent_config.get().model.maxContextLength == cfg.chat_small_context
        assert runtime.agent_config.get() is active
        assert runtime.runtime_llm.get() is None
    finally:
        runtime.agent_config.reset(token)


@pytest.mark.asyncio
async def test_parallel_choices_do_not_leak():
    cfg = _base_settings()
    async def probe(code):
        async with selected_model(code):
            await asyncio.sleep(0)
            return get_llm().settings.llm_model
    assert await asyncio.gather(probe(cfg.llm_model), probe(cfg.chat_small_model)) == [cfg.llm_model, cfg.chat_small_model]
    assert runtime.runtime_llm.get() is None


@pytest.mark.asyncio
async def test_unknown_model_rejected_and_default_unchanged():
    with pytest.raises(IntegrationError):
        async with selected_model('unregistered'):
            pass
    async with selected_model(None):
        assert runtime.runtime_llm.get() is None
    assert len(model_choices()) == 2


@pytest.mark.asyncio
async def test_system_prompt_precedes_user_message():
    cfg = _base_settings()
    async with selected_model(cfg.chat_small_model):
        original = [{'role': 'user', 'content': 'Xin chào'}]
        payload = get_llm()._payload(original, model=None, temperature=None,
            max_tokens=32, stream=False, extra=None, thinking=False)
        assert payload['messages'][0]['role'] == 'system'
        assert original[0]['role'] == 'user'
