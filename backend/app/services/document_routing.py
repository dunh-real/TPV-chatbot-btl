"""Nghiệp vụ đề xuất phân luồng văn bản đến.

LLM chỉ được trả về mã trong các danh mục đã đưa vào prompt. Mọi quyết định có
tính ràng buộc đều được thực hiện lại ở backend trước khi ghi CSDL.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.models import DocumentRouting
from app.rag.converter import DocumentConverter, SUPPORTED_SUFFIXES
from app.schemas.routing import RoutingAiResult
from app.services import storage
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

STATUS_SUGGESTED = "AI_SUGGESTED"
STATUS_REVIEW = "NEEDS_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_ROUTED = "ROUTED"
PROMPT_VERSION = "document-routing-v1"
DEFAULT_URGENCY_LEVELS = ("Thuong", "Khan", "Hoa toc")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _strip_internal_url(value: str) -> str:
    return value.removeprefix("internal://")


async def extract_content(file_url: str, extracted_text: str) -> tuple[str, str, list[str], str]:
    """Resolve file qua storage và trích chữ ngoài event loop."""
    if extracted_text.strip():
        return extracted_text.strip(), "text", [], ""
    try:
        ref = storage.resolve(_strip_internal_url(file_url))
    except storage.StorageError:
        raise
    if ref.suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Định dạng chưa hỗ trợ: {ref.suffix or '(không rõ)'}")
    try:
        loaded = await anyio.to_thread.run_sync(lambda: DocumentConverter().convert(ref.path))
    except Exception as exc:  # noqa: BLE001 - lưu NEEDS_REVIEW ở tầng gọi
        logger.exception("Không trích xuất được %s", ref.name)
        return "", ref.suffix.removeprefix("."), [f"Không đọc được file: {exc}"], ref.file_id
    warnings = [] if loaded.text.strip() else ["Không trích xuất được nội dung văn bản"]
    return loaded.text.strip(), ref.suffix.removeprefix("."), warnings, ref.file_id


def build_evidence(text: str, limit: int = 5) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, line in enumerate((part.strip() for part in text.splitlines()), start=1):
        if line and len(line) >= 8:
            evidence.append({"quote": line[:500], "page": None, "block_id": f"P{index:02d}"})
        if len(evidence) >= limit:
            break
    return evidence


def _verified_evidence(raw: Any, text: str) -> list[dict[str, Any]]:
    """Chỉ giữ trích dẫn thực sự có trong nội dung đã trích xuất."""
    if not isinstance(raw, list):
        return []
    normal_text = " ".join(text.split()).casefold()
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        if quote and " ".join(quote.split()).casefold() in normal_text:
            result.append({"quote": quote[:500], "page": item.get("page"),
                           "block_id": str(item.get("block_id") or "")})
    return result


def _prompt(
    text: str, departments: list[dict[str, Any]], categories: list[dict[str, Any]], urgency: list[str]
) -> tuple[str, str]:
    system = """Bạn là bộ máy đề xuất phân luồng văn bản đến. Chỉ trả về một JSON object, không markdown.

Nhiệm vụ: Phân tích kỹ nội dung văn bản và trả về đầy đủ các thuộc tính phân luồng:
1. document_type_code: Loại văn bản (chọn 1 mã chuẩn: CONG_VAN_DEN, QUYET_DINH, TO_TRINH, THONG_BAO, DON_THU, BAO_CAO, KE_HOACH, CONG_VAN, DE_XUAT). Không để null nếu xác định được loại.
2. field_code: Lĩnh vực văn bản (BẮT BUỘC chọn từ danh sách categories được cung cấp).
3. urgency_level: Mức độ khẩn (BẮT BUỘC chọn từ danh sách urgency_levels, ví dụ: 'Thuong', 'Khan', 'Hoa toc').
4. primary_unit_code: Đơn vị chủ trì tiếp nhận và xử lý chính (BẮT BUỘC chọn từ danh sách departments được cung cấp).
5. coordinating_unit_codes: Danh sách các đơn vị phối hợp (mã code từ departments, không trùng với đơn vị chủ trì).
6. reason: Lý do đề xuất phân luồng rõ ràng, nêu căn cứ phân luồng nghiệp vụ.
7. evidence: Bằng chứng trích dẫn nguyên văn từ nội dung văn bản (mỗi mục: {quote, page, block_id}).
8. confidence: Độ tin cậy của đề xuất (số thực 0.0 đến 1.0).

Schema JSON:
{
  "document_type_code": "string",
  "field_code": "string",
  "urgency_level": "string",
  "primary_unit_code": "string",
  "coordinating_unit_codes": ["string"],
  "reason": "string",
  "evidence": [{"quote": "string", "page": null, "block_id": "P01"}],
  "confidence": 0.95
}
"""
    user = _json({
        "departments": departments,
        "categories": categories,
        "urgency_levels": urgency,
        "document": text[:12000],
    })
    return system, user


def _map_result(
    raw: dict[str, Any], departments: list[dict[str, Any]], categories: list[dict[str, Any]], urgency: list[str],
    evidence: list[dict[str, Any]], warnings: list[str],
) -> dict[str, Any]:
    by_code = {str(item.get("code", "")): item for item in departments if item.get("code")}
    category_codes = {str(item.get("field_code", "")) for item in categories}
    primary_code = str(raw.get("primary_unit_code") or "").strip() or None
    coordinating_codes = [str(code).strip() for code in (raw.get("coordinating_unit_codes") or []) if str(code).strip()]
    result_warnings = list(warnings)
    primary = by_code.get(primary_code or "")
    if primary_code and primary is None:
        result_warnings.append(f"Không map được đơn vị chủ trì: {primary_code}")
        primary_code = None
    coordinating_codes = list(dict.fromkeys(code for code in coordinating_codes if code != primary_code))
    valid_coordinating = [code for code in coordinating_codes if code in by_code]
    for code in coordinating_codes:
        if code not in by_code:
            result_warnings.append(f"Không map được đơn vị phối hợp: {code}")
    field_code = str(raw.get("field_code") or "").strip() or None
    if field_code and field_code not in category_codes:
        result_warnings.append(f"FieldCode không có trong danh mục: {field_code}")
        field_code = None
    urgency_level = str(raw.get("urgency_level") or "").strip() or None
    if urgency_level and urgency_level not in urgency:
        result_warnings.append(f"UrgencyLevel không hợp lệ: {urgency_level}")
        urgency_level = None
    ai_evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else evidence
    ai_evidence = [item for item in ai_evidence if isinstance(item, dict) and str(item.get("quote", "")).strip()]
    reason = str(raw.get("reason") or "").strip()
    confidence = raw.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    if not reason or not ai_evidence:
        result_warnings.append("Thiếu lý do hoặc bằng chứng trích dẫn")
    if confidence is None or confidence < 0.6:
        result_warnings.append("Confidence thấp hoặc không có")
    status = STATUS_SUGGESTED if not result_warnings else STATUS_REVIEW
    return {
        "document_type_code": str(raw.get("document_type_code") or "").strip() or None,
        "field_code": field_code, "urgency_level": urgency_level,
        "ai_primary_unit_id": primary.get("id") if primary else None,
        "ai_primary_unit_code": primary_code,
        "ai_coordinating_units_json": _json([by_code[code]["id"] for code in valid_coordinating]),
        "ai_coordinating_unit_codes_json": _json(valid_coordinating),
        "ai_reason": reason, "ai_evidence_json": _json(ai_evidence),
        "ai_confidence": confidence, "warnings_json": _json(result_warnings), "status": status,
    }


async def suggest(
    *, document_id: str, file_url: str, extracted_text: str, departments: list[dict[str, Any]],
    categories: list[dict[str, Any]], urgency: list[str], tenant_id: int,
) -> DocumentRouting:
    text, source_format, warnings, file_id = await extract_content(file_url, extracted_text)
    routing = DocumentRouting(
        routing_id=f"rt_{uuid.uuid4().hex}", tenant_id=tenant_id, document_id=document_id,
        file_id=file_id or file_url, department_catalog_json=_json(departments),
        category_catalog_json=_json(categories), source_format=source_format,
        prompt_version=PROMPT_VERSION, model=get_settings().llm_model,
        status=STATUS_REVIEW, warnings_json=_json(warnings),
    )
    if not text:
        return routing
    try:
        system, user = _prompt(text, departments, categories, urgency)
        raw = await get_llm().chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1, max_tokens=900, thinking=False,
        )
        if not isinstance(raw, dict):
            raise LLMError("LLM không trả về JSON object")
        normalized = dict(raw)
        normalized["evidence"] = [
            {**item, "quote": item.get("quote") or item.get("text") or ""}
            for item in (raw.get("evidence") or [])
            if isinstance(item, dict)
        ]
        parsed = RoutingAiResult.model_validate(normalized)
        verified = _verified_evidence([item.model_dump() for item in parsed.evidence], text)
        mapped = _map_result(parsed.model_dump(), departments, categories, urgency, verified, warnings)
        for key, value in mapped.items():
            setattr(routing, key, value)
    except Exception as exc:
        logger.warning("Đề xuất routing thất bại: %s", exc)
        routing.warnings_json = _json([*warnings, f"AI không trả được kết quả hợp lệ: {exc}"])
    return routing


def as_dict(row: DocumentRouting) -> dict[str, Any]:
    def loads(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return fallback
    return {
        "RoutingId": row.routing_id, "document_id": row.document_id, "FileId": row.file_id,
        "Status": row.status, "DocumentTypeCode": row.document_type_code, "FieldCode": row.field_code,
        "UrgencyLevel": row.urgency_level, "AiPrimaryUnitId": row.ai_primary_unit_id,
        "AiPrimaryUnitCode": row.ai_primary_unit_code,
        "AiCoordinatingUnitsJson": row.ai_coordinating_units_json,
        "AiCoordinatingUnitCodes": loads(row.ai_coordinating_unit_codes_json, []),
        "AiReason": row.ai_reason,
        "AiEvidenceJson": loads(row.ai_evidence_json, []), "AiConfidence": row.ai_confidence,
        "Warnings": loads(row.warnings_json, []),
        "DepartmentCatalog": loads(row.department_catalog_json, []),
        "CategoryCatalog": loads(row.category_catalog_json, []),
        "CreatedAt": row.created_at.isoformat() if row.created_at else None,
        "ReviewedBy": row.reviewed_by, "ReviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "ReviewNote": row.review_note,
    }