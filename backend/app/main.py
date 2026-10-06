"""Điểm vào FastAPI của backend TPV chatbot."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import (agent, chat, documents, mindmap, ocr, presenton_proxy,
                     presentations, reports)
from app.api.identity import IdentityMiddleware
from app.core.config import get_settings
from app.agents.quyen import ThieuQuyen
from app.core.context import current_principal
from app.db.erp_session import dispose_erp_engine
from app.db.session import dispose_engine, healthcheck
from app.schemas.common import HealthResponse, RequestCheckResponse
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
                "yêu cầu về một trong năm workflow: hỏi đáp, soát tài liệu, soạn văn bản, "
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
app.include_router(mindmap.router)
app.include_router(ocr.router)


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


@app.get("/health/request", response_model=RequestCheckResponse, tags=["system"],
         summary="Server nhận được request này ở dạng nào")
async def health_request(request: Request) -> RequestCheckResponse:
    """Soi đúng những gì server nhận được, để bên gọi tự chẩn đoán.

    `/health` trả lời "backend còn sống không", `/whoami` trả lời "backend nghĩ
    tôi là ai". Endpoint này trả lời câu thứ ba, và là câu tốn thời gian nhất khi
    tích hợp: "request của tôi tới nơi ở dạng nào".

    Lý do cần: trình duyệt bị CORS chặn KHÔNG trả về mã lỗi nào cho JavaScript -
    `fetch` chỉ ném `TypeError: Failed to fetch`. Mọi nguyên nhân khác nhau hẳn
    (server chết, sai địa chỉ, sai scheme, bị chặn origin) đều hiện ra cùng một
    câu "lỗi kết nối", nên đội giao diện không có gì để lần. Mở thẳng URL này
    trong một tab trình duyệt thì là điều hướng cùng origin, CORS không áp dụng,
    nên nó LUÔN trả lời được - kể cả khi mọi lời gọi fetch đang bị chặn.

    Không dội lại toàn bộ header: API này mở ra Internet và chưa có chốt token,
    nên một endpoint dội header là chỗ rò `Cookie`/`Authorization`. Chỉ trả về
    danh sách đã chọn, và danh sách origin đã khai thì chỉ trả cho lời gọi từ
    chính máy chủ (xem `tu_may_chu` bên dưới).
    """
    h = request.headers
    origin = h.get("origin")

    # Qua Cloudflare thì `request.client.host` luôn là 127.0.0.1 (tunnel nối vào
    # loopback), nên chỉ mình nó không phân biệt được người gọi ở đâu. Phải xét
    # thêm header của proxy.
    fwd_for, cf_ip = h.get("x-forwarded-for", ""), h.get("cf-connecting-ip", "")
    qua_proxy = bool(fwd_for or cf_ip)
    client_ip = cf_ip or fwd_for.split(",")[0].strip() or (
        request.client.host if request.client else "")
    tu_may_chu = not qua_proxy and client_ip in ("127.0.0.1", "::1", "testclient")

    scheme = h.get("x-forwarded-proto") or request.url.scheme
    cho_phep = None if origin is None else (
        "*" in settings.cors_origins or origin in settings.cors_origins)

    chuan_doan: list[str] = []
    if origin is None:
        chuan_doan.append(
            "Request không kèm header Origin, tức không phải gọi từ trang web "
            "(curl, Postman, hoặc server gọi server). CORS không áp dụng.")
    elif cho_phep:
        chuan_doan.append(f"Origin {origin} nằm trong danh sách cho phép - CORS không chặn.")
    else:
        chuan_doan.append(
            f"Origin {origin} KHÔNG có trong CORS_ORIGINS, nên trình duyệt sẽ "
            f"chặn và giao diện chỉ thấy 'Failed to fetch'. Thêm ĐÚNG chuỗi "
            f"'{origin}' vào CORS_ORIGINS trong backend/.env rồi khởi động lại "
            f"backend. Chuỗi này không có dấu '/' ở cuối và không có đường dẫn - "
            f"chép nguyên văn, đừng chép từ thanh địa chỉ.")

    if origin and origin.startswith("https://") and scheme == "http":
        chuan_doan.append(
            "Trang chạy HTTPS nhưng gọi API qua HTTP: trình duyệt chặn mixed "
            "content trước cả khi request rời máy. Đổi địa chỉ API sang https://.")

    principal = current_principal()
    if settings.trust_identity_headers and principal.source == "default":
        chuan_doan.append(
            f"Không nhận được X-Tenant-Id, đang dùng tenant mặc định "
            f"{principal.tenant_id} của .env. Số liệu trả về sẽ là của tenant này.")

    # Danh sách origin đã khai chỉ trả cho lời gọi từ chính máy chủ: biết chính
    # xác những origin nào được tin là thông tin có ích cho người tấn công, mà
    # người gỡ lỗi từ xa không cần - họ chỉ cần biết origin CỦA HỌ có qua không.
    da_khai = list(settings.cors_origins) if tu_may_chu else None
    if tu_may_chu:
        hong = [o for o in settings.cors_origins
                if o != "*" and (o.endswith("/") or o.strip() != o
                                 or "<" in o or ">" in o or o.count("/") != 2)]
        if hong:
            chuan_doan.append(
                f"CORS_ORIGINS có {len(hong)} dòng sai định dạng nên sẽ không bao "
                f"giờ khớp: {hong}. Origin chỉ gồm scheme://host[:port].")

    return RequestCheckResponse(
        origin=origin, cors_cho_phep=cho_phep, cors_da_khai=da_khai,
        host=h.get("host", ""), scheme=scheme, qua_proxy=qua_proxy,
        client_ip=client_ip, method=request.method,
        danh_tinh={"tenant_id": principal.tenant_id, "user_id": principal.user_id,
                   "source": principal.source},
        chuan_doan=chuan_doan,
        server_time=datetime.now().isoformat(timespec="seconds"),
    )


@app.exception_handler(ThieuQuyen)
async def _thieu_quyen(request: Request, exc: ThieuQuyen) -> JSONResponse:
    """Thiếu quyền là 403, không phải 500.

    Trả về cùng hình dạng `{"detail": ...}` như mọi lỗi khác, và câu chữ đã nêu
    rõ thiếu quyền nào - giao diện hiện thẳng được, không phải dịch lại.
    """
    logger.info("403 %s - %s", request.url.path, current_principal().describe())
    return JSONResponse(status_code=403, content={"detail": exc.message})


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
