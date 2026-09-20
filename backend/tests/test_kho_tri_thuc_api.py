"""Hai endpoint nạp tài liệu phải trả về được kết quả, không nổ ở bước dựng response.

`IngestResult` là `@dataclass(slots=True)`. Dataclass có `slots=True` thì **không
có `__dict__`**, nên `IngestResponse(**result.__dict__)` ném `AttributeError` và
FastAPI trả 500 - toàn bộ màn "Kho tri thức" chết, cả `/upload` lẫn
`/ingest-text`.

Lỗi này lọt qua 447 test vì không test nào chạm tầng API của `documents`: các
test cũ gọi thẳng `IngestionPipeline`, mà pipeline thì đúng - chỗ sai nằm ở
đoạn chuyển `IngestResult` sang `IngestResponse`.

Bài test cố ý đi qua HTTP thay vì gọi hàm handler: nếu gọi hàm thì vẫn phải tự
dựng `UploadFile`, và cái bẫy `__dict__` chỉ lộ ra khi response thật sự được
dựng. Pipeline được thay bằng bản giả - không cần Qdrant, không cần model nhúng.
"""

from __future__ import annotations

import io

import httpx
import pytest

from app.api import documents as documents_api
from app.main import app
from app.rag.ingestion import IngestResult


@pytest.fixture
def pipeline_gia(monkeypatch):
    """Pipeline giả trả về đúng kiểu `IngestResult` mà bản thật trả về."""
    ket_qua = IngestResult(
        doc_id="abc123",
        doc_title="CV-105-BGD",
        chunk_count=7,
        elapsed_ms=12.5,
        source="CV-105-BGD.md",
    )

    class PipelineGia:
        async def ingest_file(self, path, doc_type=""):
            return ket_qua

        async def ingest_text(self, **kwargs):
            return ket_qua

    monkeypatch.setattr(documents_api, "get_ingestion_pipeline", lambda: PipelineGia())
    return ket_qua


async def _post(path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


async def test_upload_tra_ve_ket_qua_nap(pipeline_gia, tmp_path):
    res = await _post(
        "/api/documents/upload",
        files={"file": ("CV-105-BGD.md", io.BytesIO(b"# Cong van 105"), "text/markdown")},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["doc_id"] == "abc123"
    assert body["chunk_count"] == 7
    assert body["doc_title"] == "CV-105-BGD"


async def test_ingest_text_tra_ve_ket_qua_nap(pipeline_gia):
    res = await _post(
        "/api/documents/ingest-text",
        json={"text": "Nội dung công văn", "doc_title": "CV-105-BGD"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["chunk_count"] == 7


def test_ingest_result_khong_co_dunder_dict():
    """Chốt lại nguyên nhân gốc, để ai bỏ `slots=True` cũng biết vì sao nó ở đây."""
    ket_qua = IngestResult(doc_id="x", doc_title="y", chunk_count=1, elapsed_ms=1.0)

    with pytest.raises(AttributeError):
        _ = ket_qua.__dict__
