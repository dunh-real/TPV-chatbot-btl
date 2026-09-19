"""Điểm vào FastAPI của backend TPV chatbot."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import agent, chat, documents, presenton_proxy, presentations, reports
from app.api.identity import IdentityMiddleware
from app.core.config import get_settings
from app.core.context import current_principal
from app.db.erp_session import dispose_erp_engine
from app.db.session import dispose_engine, healthcheck
from app.schemas.common import HealthResponse
from app.services.cache import get_cache
from app.services.conversation import get_memory
from app.services.llm import get_llm
from app.rag.vectorstore import get_vector_store

logger = logging.getLogger(__name__)
settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)


async def _warmup() -> None:
    """Nạp trước embedding + reranker để request đầu tiên không phải chờ."""
    import anyio

    def _load() -> None:
        from app.rag.embedding import get_embedder
        from app.rag.reranker import get_reranker

        get_embedder().encode(["khởi động"])
        get_reranker().score("khởi động", ["khởi động"])

    try:
        await anyio.to_thread.run_sync(_load)
        logger.info("Đã nạp sẵn embedding + reranker")
    except Exception as exc:  # noqa: BLE001
        logger.error("Warm-up model thất bại: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_vector_store()
    conversations = get_memory().store
    try:
        await store.ensure_collection()
        logger.info("Qdrant sẵn sàng: collection=%s", store.collection)
    except Exception as exc:  # noqa: BLE001 - vẫn cho app chạy để /health báo lỗi
        logger.error("Không khởi tạo được Qdrant: %s", exc)
    try:
        await conversations.ensure_collection()
        logger.info("Collection hội thoại sẵn sàng: %s", conversations.collection)
    except Exception as exc:  # noqa: BLE001
        # Không chặn app, nhưng phải kêu to: đây là bản gốc của lịch sử chat,
        # hỏng mà im lặng thì hội thoại mất mà không ai biết.
        logger.error("KHÔNG khởi tạo được collection hội thoại %s - lịch sử chat sẽ "
                     "không được lưu bền: %s", conversations.collection, exc)

    if settings.warmup_models:
        await _warmup()

    yield

    await store.close()
    await conversations.close()
    await presenton_proxy.close_client()
    await get_llm().close()
    await get_cache().close()
    await dispose_engine()
    await dispose_erp_engine()


app = FastAPI(
    title=settings.app_name,
    description="Multi-agent backend (LangGraph). POST /api/agent/chat tự định tuyến "
                "yêu cầu về một trong năm workflow: hỏi đáp, soát văn bản, soạn văn bản, "
                "tổng hợp báo cáo, tạo slide.",
    version="0.1.0",
    lifespan=lifespan,
)

# Danh tính phải được đặt TRƯỚC khi handler chạy, và mọi handler đều cần nó - nên
# đây là middleware chứ không phải dependency gắn vào từng route: quên một route
# nghĩa là route đó lặng lẽ đọc số liệu của tenant mặc định.
app.add_middleware(IdentityMiddleware)
if settings.trust_identity_headers:
    logger.warning(
        "Đang tin header X-Tenant-Id/X-User-Id: client tự khai được tenant bất kỳ. "
        "Đặt TRUST_IDENTITY_HEADERS=false khi giao diện đã có đăng nhập."
    )

# Thêm sau cùng = nằm ngoài cùng, nên cả phản hồi lỗi của IdentityMiddleware
# cũng có header CORS. Ngược lại trình duyệt chỉ thấy "network error".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ACCESS_COOKIE = "tpv_access"

if settings.public_access_token:
    # Quick tunnel của Cloudflare không gắn được Access, nên chặn ở tầng ứng dụng:
    # không có nó thì bất kỳ ai biết URL đều gọi được cả /api/documents/{id} (DELETE)
    # lẫn cả GPU phía sau.
    @app.middleware("http")
    async def require_access_token(request: Request, call_next):
        expected = settings.public_access_token
        supplied = (
            request.headers.get("x-access-token")
            or request.query_params.get("token")
            or request.cookies.get(ACCESS_COOKIE)
            or ""
        )
        try:
            ok = secrets.compare_digest(supplied, expected)
        except TypeError:  # token có ký tự ngoài ASCII
            ok = supplied == expected
        if not ok:
            return JSONResponse(status_code=401,
                                content={"detail": "Thiếu hoặc sai token truy cập"})

        response = await call_next(request)
        # Vào bằng ...?token=... một lần rồi thì cookie lo phần còn lại, không phải
        # nhét token vào từng lời gọi API của trang.
        if request.query_params.get("token") == expected:
            response.set_cookie(ACCESS_COOKIE, expected, httponly=True,
                                samesite="lax", max_age=86400)
        return response

    logger.info("Đã bật chốt token truy cập cho toàn bộ API")

# Giao diện demo: phục vụ ngay từ backend để không phải dựng thêm web server và
# không phải mở CORS - mọi lời gọi API đều cùng origin với trang.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class FreshStaticFiles(StaticFiles):
    """Giao diện phải luôn là bản mới nhất, không phải bản Cloudflare nhớ được.

    Mặc định Cloudflare coi .js/.css là tài nguyên tĩnh và giữ ở biên hàng chục
    phút. Với một trang web bình thường thì đó là quà; với trang demo đang sửa
    liên tục thì đó là bẫy: backend đã có mã mới mà người dùng vẫn chạy mã cũ,
    và không ai nghĩ tới cache khi đi tìm lỗi.

    `no-cache` KHÔNG phải là "đừng lưu" mà là "lưu thì lưu, nhưng phải hỏi lại
    trước khi dùng". Kèm ETag sẵn có của StaticFiles, lần hỏi lại nào không đổi
    cũng chỉ tốn một 304 rỗng - không đánh đổi băng thông để lấy tính đúng đắn.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if FRONTEND_DIR.is_dir():
    app.mount("/ui", FreshStaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
else:
    logger.warning("Không thấy thư mục giao diện %s - bỏ qua mount /ui", FRONTEND_DIR)

app.include_router(agent.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(presentations.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    store = get_vector_store()
    qdrant_ok, points = False, 0
    try:
        points = await store.count()
        qdrant_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Health check Qdrant lỗi: %s", exc)

    conversations = get_memory().store
    conversation_turns = 0
    try:
        conversation_turns = await conversations.count()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Health check collection hội thoại lỗi: %s", exc)

    llm_ok = await get_llm().health()
    database_ok = await healthcheck()
    return HealthResponse(
        status="ok" if (qdrant_ok and llm_ok and database_ok) else "degraded",
        qdrant=qdrant_ok,
        llm=llm_ok,
        database=database_ok,
        collection=store.collection,
        points=points,
        details={"llm_model": settings.llm_model, "embedding_model": settings.embedding_model,
                 "reranker_model": settings.reranker_model,
                 "conversation_collection": conversations.collection,
                 "conversation_turns": str(conversation_turns)},
    )


@app.get("/whoami", tags=["system"], summary="Backend đang thấy request này là ai")
async def whoami() -> dict[str, object]:
    """Danh tính mà backend đọc ra từ request, để đội làm giao diện tự đối chiếu.

    Không có nó thì lỗi tích hợp phổ biến nhất - gửi thiếu header rồi nhận số liệu
    của tenant mặc định - chỉ lộ ra khi ai đó thấy con số trong báo cáo là lạ.
    """
    principal = current_principal()
    return {
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        # "token" = lấy từ tài khoản đăng nhập, "header" = client tự khai,
        # "default" = client không khai gì và đang dùng ERP_TENANT_ID của .env.
        "source": principal.source,
        "trust_identity_headers": settings.trust_identity_headers,
    }


@app.get("/", tags=["system"], include_in_schema=False)
async def root():
    """Vào thẳng giao diện nếu có, không thì trả về thông tin dịch vụ."""
    if FRONTEND_DIR.is_dir():
        return RedirectResponse(url="/ui/")
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}


# PHẢI đăng ký sau cùng. Proxy có một tuyến bắt ảnh ở gốc (`/{asset}`), xếp trước
# thì nó đứng chắn `/health`, `/docs`, `/openapi.json` - Starlette khớp tuyến theo
# đúng thứ tự khai báo.
app.include_router(presenton_proxy.router)
