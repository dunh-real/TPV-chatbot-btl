"""
Health Check Endpoint
Kiểm tra trạng thái các services: Qdrant, Redis, Ollama
"""

from fastapi import APIRouter
from datetime import datetime
import httpx

from src.core.config import settings
from src.models.schemas import HealthCheckResponse

router = APIRouter()

def _check_qdrant() -> bool:
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url = settings.qdrant_url, timeout = 3)
        client.get_collection()
        return True
    except Exception:
        return False

def _check_redis() -> bool:
    try:
        import redis
        r = redis.Redis(
            host = settings.redis_host,
            port = settings.redis_port,
            db = settings.redis_db,
            password = settings.redis_password,
            socket_timeout = 3,
        )
        return r.ping()
    except Exception:
        return False

def _check_ollama() -> bool:
    try:
        resp = httpx.get(f"{settings.llm_base_url}/api/tags", timeout = 3)
        return resp.status_code == 200
    except Exception:
        return False

@router.get("/health", response_model = HealthCheckResponse)
async def health_check():
    """Kiểm tra trạng thái hệ thống"""
    services = {
        "qdrant": _check_qdrant(),
        "redis": _check_redis(),
        "ollama": _check_ollama(),
    }
    
    all_ok = all(services.values())
    
    return HealthCheckResponse(
        status = "healthy" if all_ok else "degraded",
        timestamp = datetime.now(),
        services = services,
        version = "1.0.0",
    )