"""API sơ đồ tư duy: dựng cây chủ đề từ một tài liệu trong kho.

Chia làm hai nhịp, và đó là điểm mấu chốt của tính năng:

    POST /generate            đọc CẢ tài liệu -> khung cây (chỉ tiêu đề)
    POST /{doc_id}/section    bấm vào một mục -> truy hồi + viết nội dung mục đó

Sinh sẵn nội dung cho cả cây lúc dựng là 30-60 lượt LLM cho thứ người dùng phần
lớn không mở ra xem. Nên nội dung sinh khi được hỏi, rồi ghi lại vào cây - lần
sau bấm vào đúng mục đó là trả ngay, không tốn lượt nào.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents import quyen
from app.documents.mindmap import (
    build_batches,
    count_nodes,
    find_node,
    generate_node_content,
    generate_skeleton,
    strip_for_api,
)
from app.rag.retrieval import get_retriever
from app.rag.vectorstore import build_filter, get_vector_store
from app.schemas.mindmap import (
    GenerateRequest,
    MindmapResponse,
    MindmapSource,
    MindmapSummary,
    SectionRequest,
    SectionResponse,
)
from app.services import mindmap_store
from app.services.llm import LLMError
from app.services.mindmap_store import MindmapStoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mindmap", tags=["mindmap"])

DOC_READ = Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xem sơ đồ tư duy"))

# Dưới mức này thì cây không đáng gọi là sơ đồ - thường là model trả về rác hoặc
# tài liệu quá ngắn. Báo lỗi còn hơn lưu lại một cây trống rồi để người dùng
# tưởng tài liệu của mình chỉ có ngần ấy nội dung.
MIN_NODES = 3


def _record_or_404(doc_id: str) -> dict:
    try:
        record = mindmap_store.load(doc_id)
    except MindmapStoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chưa có sơ đồ tư duy cho tài liệu '{doc_id}'. Hãy dựng trước.",
        )
    return record


def _response(record: dict, *, cached: bool) -> MindmapResponse:
    tree = record["tree"]
    return MindmapResponse(
        doc_id=record.get("doc_id", ""),
        doc_title=record.get("doc_title") or tree.get("title", ""),
        node_count=count_nodes(tree),
        tree=strip_for_api(tree),
        saved_at=record.get("saved_at"),
        cached=cached,
    )


@router.get("/sources", response_model=list[MindmapSource], dependencies=[DOC_READ],
            summary="Tài liệu trong kho để chọn dựng sơ đồ")
async def sources() -> list[MindmapSource]:
    try:
        documents = await get_vector_store().list_documents()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Không đọc được kho tài liệu: {exc}") from exc

    built = {item["doc_id"] for item in mindmap_store.list_all()}
    return [
        MindmapSource(**doc, has_mindmap=doc["doc_id"] in built)
        for doc in documents
    ]


@router.get("/documents", response_model=list[MindmapSummary], dependencies=[DOC_READ],
            summary="Các sơ đồ tư duy đã dựng")
async def list_documents() -> list[MindmapSummary]:
    return [MindmapSummary(**item) for item in mindmap_store.list_all()]


@router.post("/generate", response_model=MindmapResponse, dependencies=[DOC_READ],
             summary="Dựng sơ đồ tư duy từ một tài liệu trong kho")
async def generate(request: GenerateRequest) -> MindmapResponse:
    doc_id = request.doc_id.strip()
    if not doc_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Thiếu doc_id của tài liệu cần dựng sơ đồ")

    try:
        existing = mindmap_store.load(doc_id)
    except MindmapStoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Dựng lại tốn 5-9 lượt LLM, nên mặc định là trả bản đã có. Muốn bản mới thì
    # phải nói rõ.
    if existing and not request.regenerate:
        return _response(existing, cached=True)

    try:
        payloads = await get_vector_store().scroll_document(doc_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Không đọc được tài liệu từ kho: {exc}") from exc

    texts = [str(p.get("text") or "").strip() for p in payloads]
    texts = [t for t in texts if t]
    if not texts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy nội dung của tài liệu '{doc_id}' trong kho.",
        )

    doc_title = str(payloads[0].get("doc_title") or payloads[0].get("source") or doc_id)
    batches = build_batches(texts)
    word_count = sum(len(t.split()) for t in texts)
    logger.info("Dựng sơ đồ tư duy cho %s: %d chunk, %d mẻ, ~%d từ",
                doc_title, len(texts), len(batches), word_count)

    try:
        tree = await generate_skeleton(batches, doc_title, word_count)
    except LLMError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"LLM không dựng được sơ đồ tư duy: {exc}") from exc

    nodes = count_nodes(tree)
    if nodes < MIN_NODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Sơ đồ dựng ra quá sơ sài ({nodes} mục). Tài liệu quá ngắn, "
                   f"hoặc model trả về không dùng được - thử dựng lại.",
        )

    mindmap_store.save(doc_id, tree, meta={"doc_title": doc_title, "word_count": word_count})
    return _response(_record_or_404(doc_id), cached=False)


@router.get("/{doc_id}", response_model=MindmapResponse, dependencies=[DOC_READ],
            summary="Đọc lại sơ đồ tư duy đã dựng")
async def get_mindmap(doc_id: str) -> MindmapResponse:
    return _response(_record_or_404(doc_id), cached=True)


@router.post("/{doc_id}/section", response_model=SectionResponse, dependencies=[DOC_READ],
             summary="Nội dung chi tiết của một mục")
async def section(doc_id: str, request: SectionRequest) -> SectionResponse:
    record = _record_or_404(doc_id)
    tree = record["tree"]

    node = find_node(tree, request.node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Không có mục '{request.node_id}' trong sơ đồ này")

    title = str(node.get("title") or "")
    if node.get("summary"):
        return SectionResponse(
            doc_id=doc_id, node_id=request.node_id, title=title,
            summary=node["summary"], key_points=node.get("key_points", []),
            sources=node.get("sources", []), cached=True,
        )

    # Truy hồi trong ĐÚNG tài liệu này. Bỏ bộ lọc doc_id thì mục "Kết luận" sẽ
    # lấy về phần kết luận của một văn bản khác mà không ai nhận ra.
    query_filter = build_filter(doc_ids=[doc_id])
    result = await get_retriever().retrieve(query=title, query_filter=query_filter)

    # Ngưỡng rerank trả lời câu "kho có nói về chuyện này không". Ở đây câu đó đã
    # có đáp án rồi: tiêu đề mục do chính tài liệu này đẻ ra, nên nội dung chắc
    # chắn nằm đâu đó bên trong. Để ngưỡng vứt hết đi rồi báo "không tìm thấy nội
    # dung" là tự mâu thuẫn với cái cây mình vừa dựng. Nên khi rỗng thì hỏi lại
    # không rerank: xếp hạng kém hơn, nhưng vẫn là những đoạn khớp nhất TRONG
    # đúng tài liệu ấy.
    if not result.chunks:
        logger.info("Mục %r không đoạn nào qua ngưỡng rerank - truy hồi lại không rerank", title)
        result = await get_retriever().retrieve(query=title, query_filter=query_filter, rerank=False)

    chunks = [chunk.text for chunk in result.chunks if chunk.text.strip()]
    sources = list(dict.fromkeys(chunk.citation_label() for chunk in result.chunks))

    try:
        content = await generate_node_content(title, chunks)
    except (LLMError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"LLM không viết được nội dung mục '{title}': {exc}") from exc

    # Ghi lại vào cây: lần sau bấm vào đúng mục này là trả ngay.
    node["summary"] = content["summary"]
    node["key_points"] = content["key_points"]
    node["sources"] = sources
    mindmap_store.save(doc_id, tree, meta={k: v for k, v in record.items()
                                           if k not in {"tree", "saved_at"}})

    return SectionResponse(
        doc_id=doc_id, node_id=request.node_id, title=title,
        summary=content["summary"], key_points=content["key_points"],
        sources=sources, cached=False,
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xoá sơ đồ tư duy"))],
               summary="Xoá sơ đồ tư duy đã dựng")
async def delete_mindmap(doc_id: str) -> None:
    try:
        deleted = mindmap_store.delete(doc_id)
    except MindmapStoreError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Chưa có sơ đồ tư duy cho tài liệu '{doc_id}'")
