"""API quản trị xác thực shared key tại middleware; test dữ liệu vẫn cần quyền ERP."""
from fastapi import APIRouter, Request

from app.core.context import resolve_principal, use_principal
from app.schemas.admin import AgentConfig, AgentTest, ModelTest
from app.services import agent_config, agent_test, model_registry
from app.services.integration_errors import IntegrationError

router = APIRouter(prefix="/api/admin", tags=["Quản trị AI"])


@router.get("/agents")
async def agents():
    return {"codes": sorted(agent_config.AGENTS)}


@router.get("/agents/{code}/config", response_model=AgentConfig)
async def read_config(code: str):
    config = agent_config.load(code)
    if config is None:
        raise IntegrationError("CONFIG_INVALID", "Agent chưa có cấu hình đã lưu", 404)
    return config


@router.put("/agents/{code}/config")
async def configure(code: str, config: AgentConfig):
    revision = agent_config.save(code, config)
    return {"agentCode": code, "revision": revision, "applied": True}


@router.post("/models/test")
async def test_model(request: ModelTest):
    return await model_registry.test_model(request)


@router.post("/agents/{code}/test")
async def test_agent(code: str, request: AgentTest, http_request: Request):
    agent_config.require_agent(code)
    from app.api.identity import _kem_quyen
    principal = await _kem_quyen(resolve_principal(dict(http_request.headers)))
    if http_request.headers.get("x-tenant-id") and str(principal.tenant_id) != http_request.headers["x-tenant-id"].strip():
        raise IntegrationError("TENANT_MISMATCH", "Tenant trong header không khớp tài khoản ERP", 403)
    if principal.access is None:
        raise IntegrationError("UNAUTHORIZED", "Test Agent cần tài khoản ERP hợp lệ để xác định phạm vi dữ liệu", 401)
    with use_principal(principal):
        return await agent_test.run(code, request)
