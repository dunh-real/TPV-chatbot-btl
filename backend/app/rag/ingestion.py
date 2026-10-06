"""Nạp tài liệu vào Qdrant: đọc file -> chunk -> embed 3 nhánh -> upsert.

Khâu đọc file do `app.rag.converter` lo (Markdown + bảng + OCR trang scan); ở đây
chỉ còn điều phối và ghi dữ liệu. Ingest lại cùng một `doc_id` sẽ ghi đè đúng các
point cũ (id sinh từ doc_id + chunk_index) và xoá phần dư nếu tài liệu mới ngắn hơn.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import anyio

from app.core.config import Settings, get_settings
from app.rag.chunking import Chunk, Chunker
from app.rag.doc_card import CARD_SECTION, build_card
from app.rag.converter import SUPPORTED_SUFFIXES, LoadedDocument, get_converter
from app.rag.embedding import embed_documents
from app.rag.vectorstore import ChunkPoint, QdrantVectorStore, get_vector_store

logger = logging.getLogger(__name__)

__all__ = [
    "IngestionPipeline",
    "IngestResult",
    "LoadedDocument",
    "SUPPORTED_SUFFIXES",
    "contextualize",
    "get_ingestion_pipeline",
    "make_doc_id",
    "make_doc_id_file",
]


@dataclass(slots=True)
class IngestResult:
    doc_id: str
    doc_title: str
    chunk_count: int
    elapsed_ms: float
    source: str = ""


def _page_of(offset: int, page_offsets: list[int]) -> int | None:
    """Trang chứa vị trí ký tự `offset` (đánh số từ 1)."""
    if not page_offsets:
        return None
    page = 1
    for i, start in enumerate(page_offsets):
        if offset >= start:
            page = i + 1
        else:
            break
    return page


def contextualize(doc_title: str, chunk_text: str) -> str:
    """Văn bản dùng để sinh vector / chấm điểm: tiêu đề tài liệu + nội dung chunk."""
    return f"{doc_title}\n{chunk_text}" if doc_title else chunk_text


def make_doc_id(source: str, text: str) -> str:
    """Id theo nguồn + CHỮ ĐÃ TRÍCH. Chỉ dùng khi không có file để băm.

    Đường `ingest_text` nhận chữ trần, không có file nào - đành băm chữ. Có file
    thì dùng `make_doc_id_file`, xem docstring ở đó để biết vì sao.
    """
    digest = hashlib.sha256(f"{source}|{text[:4096]}".encode("utf-8")).hexdigest()[:16]
    return digest


def make_doc_id_file(source: str, path: Path) -> str:
    """Id theo nguồn + BYTES CỦA FILE: cùng file thì luôn cùng id.

    Không băm chữ đã trích, vì chữ đã trích không tái lập được. Tài liệu scan đi
    qua OCR, mà OCR chạy trên vLLM: greedy chỉ tất định khi batch giống nhau, còn
    batch thì đổi theo tải lúc đó. Cùng một file scan nạp hai lần lệch vài ký tự
    là đủ ra hai `doc_id`, và kho có hai bản gần giống nhau tranh nhau chỗ trong
    kết quả tra cứu - đã gặp thật, cùng một file ra ba id trong một buổi tối.

    Băm theo file còn gỡ một cái bẫy thứ hai: mọi thay đổi ở `app.rag.converter`
    đều đổi chữ trích ra, tức đổi id của TOÀN BỘ tài liệu đã nạp. Nạp lại sinh
    bản mới, bản cũ nằm lại làm mồ côi, phải xoá tay từng cái. Băm theo file thì
    nạp lại là ghi đè đúng chỗ.
    """
    digest = hashlib.sha256(source.encode("utf-8") + b"|" + path.read_bytes()).hexdigest()
    return digest[:16]


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
class IngestionPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        store: QdrantVectorStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or get_vector_store()
        self.chunker = Chunker(self.settings)

    async def ingest_text(
        self,
        text: str,
        doc_title: str,
        doc_id: str | None = None,
        source: str = "",
        doc_type: str = "",
        metadata: dict[str, Any] | None = None,
        page_offsets: list[int] | None = None,
    ) -> IngestResult:
        started = time.perf_counter()
        await self.store.ensure_collection()

        doc_id = doc_id or make_doc_id(source or doc_title, text)
        base_metadata = {
            "doc_title": doc_title,
            "source": source or doc_title,
            "doc_type": doc_type,
            **(metadata or {}),
        }

        chunks = await anyio.to_thread.run_sync(lambda: self.chunker.split(text, base_metadata))
        if not chunks:
            logger.warning("Tài liệu %s không tạo được chunk nào", doc_title)
            return IngestResult(doc_id=doc_id, doc_title=doc_title, chunk_count=0,
                                elapsed_ms=(time.perf_counter() - started) * 1000, source=source)

        if self.settings.doc_card_enabled:
            chunks = self._with_doc_card(text, doc_title, base_metadata, chunks)

        # Gắn số trang bằng cách dò vị trí chunk trong văn bản gốc.
        pages = self._locate_pages(text, chunks, page_offsets or [])

        # Nhúng kèm tiêu đề tài liệu: người dùng hay hỏi thẳng theo tên văn bản
        # ("Thông tư 80 quy định gì") trong khi tên đó không xuất hiện trong thân chunk.
        embed_inputs = [contextualize(doc_title, chunk.text) for chunk in chunks]
        embeddings = await anyio.to_thread.run_sync(lambda: embed_documents(embed_inputs))

        points = [
            ChunkPoint(
                doc_id=doc_id,
                chunk_index=chunk.index,
                text=chunk.text,
                embedding=embedding,
                payload={
                    **chunk.metadata,
                    "section": chunk.section_label,
                    "sections": chunk.sections,
                    "chapter": chunk.chapter,
                    "has_table": chunk.has_table,
                    "page": page,
                    "token_count": chunk.token_count,
                    "ingested_at": time.time(),
                },
            )
            for chunk, embedding, page in zip(chunks, embeddings, pages, strict=True)
        ]

        # Xoá bản cũ trước khi ghi: tránh sót chunk thừa của lần ingest trước.
        await self.store.delete_document(doc_id)
        written = await self.store.upsert_chunks(points)

        elapsed = (time.perf_counter() - started) * 1000
        logger.info("Đã nạp %s: %d chunk trong %.0fms", doc_title, written, elapsed)
        return IngestResult(doc_id=doc_id, doc_title=doc_title, chunk_count=written,
                            elapsed_ms=elapsed, source=source)

    def _with_doc_card(
        self, text: str, doc_title: str, base_metadata: dict[str, Any], chunks: list[Chunk]
    ) -> list[Chunk]:
        """Thêm chunk "thông tin thể thức" cho văn bản hành chính.

        Chi tiết hay bị hỏi lẻ (ai ký, số mấy, ngày nào) nằm rải trong một văn bản
        dài thì chunk chứa nó không *về* chúng, và cross-encoder chấm rất thấp.
        Thẻ ngắn này thì về đúng những câu đó. Tệp không phải văn bản hành chính
        sẽ không có thẻ - `build_card` tự trả None.
        """
        card = build_card(text, doc_title)
        if card is None:
            return chunks

        logger.debug("Thẻ thông tin văn bản %s: %s", doc_title, ", ".join(card.found))
        return [
            *chunks,
            Chunk(
                text=card.text,
                index=len(chunks),
                section=CARD_SECTION,
                token_count=self.chunker.count_tokens(card.text),
                metadata={**base_metadata, "chunk_type": "the_thuc",
                          "components": card.found},
            ),
        ]

    def _locate_pages(self, text: str, chunks, page_offsets: list[int]) -> list[int | None]:
        if not page_offsets:
            return [None] * len(chunks)
        pages: list[int | None] = []
        cursor = 0
        for chunk in chunks:
            # Bỏ dòng tiêu đề mục (do chunker thêm vào) để dò đúng vị trí gốc.
            body = chunk.text.split("\n", 1)[-1] if chunk.section else chunk.text
            probe = body.strip()[:80]
            position = text.find(probe, cursor) if probe else -1
            if position == -1:
                position = text.find(probe) if probe else cursor
            if position >= 0:
                cursor = position
            pages.append(_page_of(max(position, 0), page_offsets))
        return pages

    async def ingest_file(
        self,
        path: str | Path,
        doc_id: str | None = None,
        doc_type: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> IngestResult:
        """`source` ghi đè tên dùng làm nhãn và làm khoá băm `doc_id`.

        Cần khi tên file TRÊN ĐĨA không phải tên người dùng nhìn thấy: cửa
        `/api/agent/upload` lưu kèm hậu tố GUID để hai lượt tải cùng một file
        không đè nhau, nhưng trích dẫn thì phải hiện tên thật.
        """
        path = Path(path)
        ten = source or path.name
        document = await anyio.to_thread.run_sync(lambda: get_converter().convert(path))
        if not document.text.strip():
            raise ValueError(f"Không trích xuất được nội dung từ {path.name}")
        return await self.ingest_text(
            text=document.text,
            doc_title=Path(ten).stem,
            doc_id=doc_id or make_doc_id_file(ten, path),
            source=ten,
            doc_type=doc_type or path.suffix.lstrip("."),
            metadata=metadata,
            page_offsets=document.page_offsets,
        )

    async def ingest_paths(self, paths: Iterable[str | Path]) -> list[IngestResult]:
        results: list[IngestResult] = []
        for path in paths:
            path = Path(path)
            try:
                results.append(await self.ingest_file(path))
            except Exception:  # noqa: BLE001 - một file lỗi không dừng cả lô
                logger.exception("Bỏ qua %s", path)
        return results

    async def ingest_directory(self, directory: str | Path, pattern: str = "**/*") -> list[IngestResult]:
        directory = Path(directory)
        files = [p for p in directory.glob(pattern)
                 if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES]
        logger.info("Tìm thấy %d file trong %s", len(files), directory)
        return await self.ingest_paths(files)


_pipeline: IngestionPipeline | None = None


def get_ingestion_pipeline() -> IngestionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = IngestionPipeline()
    return _pipeline
