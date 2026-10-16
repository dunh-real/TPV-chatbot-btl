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
    """ID theo bytes file; đổi tên không tạo tài liệu mới.

    Giữ tham số source để tương thích các caller hiện tại.
    """
    with path.open("rb") as content:
        return hashlib.file_digest(content, "sha256").hexdigest()[:16]


def make_legacy_doc_id_file(source: str, path: Path) -> str:
    """ID cũ theo tên + bytes, dùng tra cứu dữ liệu chưa chuyển đổi."""
    digest = hashlib.sha256(source.encode("utf-8") + b"|")
    with path.open("rb") as content:
        while block := content.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()[:16]


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

        # Văn bản quy phạm (khi bật phiên bản): cây Điều/Khoản/Điểm vào CSDL, lệnh sửa đổi
        # được áp, và mỗi khoảng hiệu lực của văn bản thành một bộ chunk riêng.
        from app.legal import hooks as legal_hooks

        legal = await legal_hooks.on_ingest(text, doc_id, source or doc_title)
        if legal is not None:
            doc_title = legal.this.doc_title
            base_metadata["doc_title"] = doc_title

        chunks = await anyio.to_thread.run_sync(lambda: self.chunker.split(text, base_metadata))
        if not chunks:
            logger.warning("Tài liệu %s không tạo được chunk nào", doc_title)
            return IngestResult(doc_id=doc_id, doc_title=doc_title, chunk_count=0,
                                elapsed_ms=(time.perf_counter() - started) * 1000, source=source)

        if legal is not None and len(legal.this.snapshots) > 1:
            # Văn bản đã bị sửa đổi: chunk từ từng bản dựng lại theo khoảng hiệu lực.
            sets = await self._snapshot_sets(legal.this, base_metadata)
        else:
            snap = legal.this.snapshots[0] if legal is not None and legal.this.snapshots else None
            sets = [(snap, chunks)]
        if self.settings.doc_card_enabled:
            snap, latest = sets[-1]
            sets[-1] = (snap, self._with_doc_card(text, doc_title, base_metadata, latest))

        # Gắn số trang bằng cách dò vị trí chunk trong văn bản gốc (chỉ bản nạp vào có trang).
        pages_of = {id(cs): self._locate_pages(text, cs, page_offsets or []) for _, cs in sets}
        written = await self._write(doc_id, doc_title, sets, pages_of,
                                    legal.this if legal is not None else None)

        # Văn bản đích vừa bị văn bản này sửa: dựng lại chunk của chúng ngay.
        for other in (legal.others if legal is not None else []):
            other_meta = {"doc_title": other.doc_title, "source": other.source,
                          "doc_type": Path(other.source).suffix.lstrip(".")}
            other_sets = await self._snapshot_sets(other, other_meta)
            n = await self._write(other.doc_id, other.doc_title, other_sets, {}, other)
            logger.info("Dựng lại %s sau sửa đổi: %d chunk / %d khoảng hiệu lực",
                        other.doc_title, n, len(other_sets))

        elapsed = (time.perf_counter() - started) * 1000
        logger.info("Đã nạp %s: %d chunk trong %.0fms", doc_title, written, elapsed)
        return IngestResult(doc_id=doc_id, doc_title=doc_title, chunk_count=written,
                            elapsed_ms=elapsed, source=source)

    async def refresh_legal_document(self, snap_doc) -> int:
        """Dựng lại chunk của một VBQPPL từ cây phiên bản (sau khi duyệt/áp một lệnh sửa đổi)."""
        await self.store.ensure_collection()
        meta = {"doc_title": snap_doc.doc_title, "source": snap_doc.source,
                "doc_type": Path(snap_doc.source).suffix.lstrip(".")}
        sets = await self._snapshot_sets(snap_doc, meta)
        return await self._write(snap_doc.doc_id, snap_doc.doc_title, sets, {}, snap_doc)

    async def _snapshot_sets(self, snap_doc, base_metadata: dict[str, Any]) -> list[tuple[Any, list[Chunk]]]:
        sets = []
        for snap in snap_doc.snapshots:
            cs = await anyio.to_thread.run_sync(lambda t=snap.text: self.chunker.split(t, dict(base_metadata)))
            sets.append((snap, cs))
        return sets

    async def _write(self, doc_id: str, doc_title: str, sets: list[tuple[Any, list[Chunk]]],
                     pages_of: dict[int, list[int | None]], snap_doc) -> int:
        """Embed + ghi mọi bộ chunk của một tài liệu (xoá bản cũ trước)."""
        from app.legal import hooks as legal_hooks

        points: list[ChunkPoint] = []
        for snapshot_index, (snap, cs) in enumerate(sets):
            if not cs:
                continue
            pages = pages_of.get(id(cs)) or [None] * len(cs)
            # Nhúng kèm tiêu đề tài liệu: người dùng hay hỏi thẳng theo tên văn bản
            # ("Thông tư 80 quy định gì") trong khi tên đó không xuất hiện trong thân chunk.
            embed_inputs = [contextualize(doc_title, chunk.text) for chunk in cs]
            embeddings = await anyio.to_thread.run_sync(lambda e=embed_inputs: embed_documents(e))
            for chunk, embedding, page in zip(cs, embeddings, pages, strict=True):
                payload = {
                    **chunk.metadata,
                    "section": chunk.section_label,
                    "sections": chunk.sections,
                    "chapter": chunk.chapter,
                    "has_table": chunk.has_table,
                    "page": page,
                    "token_count": chunk.token_count,
                    "ingested_at": time.time(),
                    # Điều chứa chunk (VBQPPL): để truy hồi mở rộng một Khoản về cả Điều (small-to-big).
                    "dieus": [str(u["dieu"]) for u in chunk.units if u.get("dieu")],
                }
                if snap_doc is not None and snap is not None:
                    payload.update(legal_hooks.chunk_payload(snap_doc, snap, chunk.units))
                points.append(ChunkPoint(doc_id=doc_id, chunk_index=chunk.index, text=chunk.text,
                                         embedding=embedding, payload=payload, snapshot=snapshot_index))

        # Xoá bản cũ trước khi ghi: tránh sót chunk thừa của lần ingest trước.
        await self.store.delete_document(doc_id)
        return await self.store.upsert_chunks(points)

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
