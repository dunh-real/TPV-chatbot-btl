"""Ngữ cảnh cấu hình Agent, truy vết và chế độ test riêng cho từng lượt gọi."""
from contextvars import ContextVar

request_id = ContextVar("request_id", default="")
agent_config = ContextVar("agent_config", default=None)
runtime_settings = ContextVar("runtime_settings", default=None)
runtime_llm = ContextVar("runtime_llm", default=None)
test_mode = ContextVar("test_mode", default=False)
metadata_filters = ContextVar("metadata_filters", default=None)
last_error = ContextVar("last_error", default=None)
