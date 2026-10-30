"""API workflow 6: đề xuất và phê duyệt phân luồng văn bản đến."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select

from app.agents import quyen
from app.core.config import get_settings
from app.core.context import current_principal, current_tenant_id
from app.db.erp_repository import ErpDonViRepository
from app.db.erp_session import erp_session_scope
from app.db.models import AICategory, DocumentRouting
from app.db.session import session_scope
from app.schemas.routing import ApproveRequest, RoutingRequest, RoutingResponse
from app.services import document_routing as service
from app.services import storage

router = APIRouter(prefix="/api/document-routings", tags=["document-routings"])
_DOC = [Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xem đề xuất phân luồng"))]
_APPROVE = [Depends(quyen.can_quyen("Ai.DocumentRouting.Approve", "phê duyệt phân luồng"))]

_DEMO_DEPARTMENTS = [
    {"id": 10, "code": "P.KT", "display_name": "Phòng Kế toán",
     "description": "Tài chính, kế toán, ngân sách, quyết toán"},
    {"id": 20, "code": "P.HCNS", "display_name": "Phòng Hành chính Nhân sự",
     "description": "Văn thư, hành chính, nhân sự, tiền lương, tổ chức cán bộ"},
    {"id": 30, "code": "P.KD", "display_name": "Phòng Kinh doanh",
     "description": "Kinh doanh, phát triển thị trường, khách hàng, bán hàng, hợp đồng thương mại"},
    {"id": 40, "code": "P.KHTH", "display_name": "Phòng Kế hoạch Tổng hợp",
     "description": "Kế hoạch sản xuất kinh doanh, tổng hợp báo cáo định kỳ, đầu tư, điều phối dự án"},
    {"id": 50, "code": "P.CNTT", "display_name": "Phòng Kỹ thuật & CNTT",
     "description": "Hạ tầng công nghệ thông tin, quản trị mạng, an ninh bảo mật, phần mềm, bảo trì kỹ thuật"},
    {"id": 60, "code": "P.PC", "display_name": "Phòng Pháp chế & Kiểm soát",
     "description": "Pháp lý, rà soát hợp đồng, kiểm soát nội bộ, thanh tra, xử lý khiếu nại tố cáo"},
]
_DEMO_CATEGORIES = [
    {"field_code": "TAI_CHINH", "field_name": "Tài chính",
     "description": "Thu chi, ngân sách, tài sản tài chính"},
    {"field_code": "NHAN_SU", "field_name": "Nhân sự",
     "description": "Cán bộ, hợp đồng, tuyển dụng, chế độ"},
]


def _tenant() -> int:
    tenant = current_tenant_id()
    if type(tenant) is not int:
        raise HTTPException(status_code=400, detail="Request phải có tenant cụ thể")
    return tenant


def _response(row: DocumentRouting) -> RoutingResponse:
    return RoutingResponse.model_validate(service.as_dict(row))


async def _erp_catalog() -> list[dict[str, Any]]:
    async with erp_session_scope() as session:
        rows = await ErpDonViRepository(session).list_all()
    return [{"id": int(row.id), "code": row.ma_don_vi, "display_name": row.display_name,
             "description": row.describe or ""} for row in rows]


def _request_departments(request: RoutingRequest) -> list[dict[str, Any]]:
    return [{"id": item.id, "code": item.code, "display_name": item.display_name,
             "description": item.description} for item in request.departments]


def _request_categories(request: RoutingRequest) -> list[dict[str, Any]]:
    return [{"field_code": item.field_code, "field_name": item.field_name,
             "description": item.description} for item in request.categories]


async def _category_catalog(tenant: int) -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (await session.execute(
            select(AICategory).where(
                AICategory.is_active.is_(True),
                or_(AICategory.tenant_id.is_(None), AICategory.tenant_id == tenant),
            ).order_by(AICategory.field_code)
        )).scalars().all()
    return [{"field_code": row.field_code, "field_name": row.field_name,
             "description": row.description} for row in rows]


@router.post("/suggest", response_model=RoutingResponse, dependencies=_DOC,
             summary="Tạo đề xuất phân luồng văn bản đến")
async def suggest(request: RoutingRequest) -> RoutingResponse:
    tenant = _tenant()
    if not request.file_url.strip() and not request.extracted_text.strip():
        raise HTTPException(status_code=422, detail="Cần FileUrl hoặc ExtractedText")

    supplied_departments = _request_departments(request)
    try:
        departments = await _erp_catalog()
    except Exception as exc:  # noqa: BLE001 - demo có thể truyền catalog đã kiểm soát
        if supplied_departments:
            departments = supplied_departments
        elif get_settings().trust_identity_headers and not get_settings().erp_database_url:
            departments = _DEMO_DEPARTMENTS
        else:
            raise HTTPException(status_code=503, detail=f"Không tải được danh mục phòng ban: {exc}") from exc
    categories = await _category_catalog(tenant)
    if not categories:
        categories = _request_categories(request) or (
            _DEMO_CATEGORIES if get_settings().trust_identity_headers and not get_settings().erp_database_url else []
        )
    document_id = request.document_id or request.file_url or "text-routing"

    async with session_scope() as session:
        current = (await session.execute(
            select(DocumentRouting).where(
                DocumentRouting.tenant_id == tenant,
                DocumentRouting.document_id == document_id,
                DocumentRouting.status.in_([service.STATUS_SUGGESTED, service.STATUS_REVIEW, service.STATUS_APPROVED]),
            ).order_by(DocumentRouting.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if current is not None and not request.force_refresh:
            return _response(current)

    try:
        row = await service.suggest(
            document_id=document_id, file_url=request.file_url, extracted_text=request.extracted_text,
            departments=departments, categories=categories, urgency=request.urgency_levels, tenant_id=tenant,
        )
    except storage.StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    async with session_scope() as session:
        if current is not None:
            current = await session.get(DocumentRouting, current.routing_id)
            if current is not None:
                current.status = service.STATUS_SUPERSEDED
                current.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(row)
        await session.flush()
        return _response(row)


@router.get("/documents", summary="Danh sách tài liệu có sẵn trong CSDL để phân luồng")
async def list_available_documents() -> list[dict[str, Any]]:
    tenant = _tenant()
    async with session_scope() as session:
        rows = (await session.execute(
            select(DocumentRouting).where(DocumentRouting.tenant_id == tenant)
            .order_by(DocumentRouting.created_at.desc())
        )).scalars().all()
        seen = set()
        docs = []
        for r in rows:
            if r.document_id and r.document_id not in seen:
                seen.add(r.document_id)
                docs.append({
                    "document_id": r.document_id,
                    "file_id": r.file_id,
                    "title": r.document_id,
                    "document_type_code": r.document_type_code,
                    "field_code": r.field_code,
                    "urgency_level": r.urgency_level,
                    "status": r.status,
                    "ai_reason": r.ai_reason,
                    "routing_id": r.routing_id,
                })
        return docs


@router.get("/{routing_id}", response_model=RoutingResponse, dependencies=_DOC,
            summary="Xem một đề xuất phân luồng")
async def get_routing(routing_id: str) -> RoutingResponse:
    async with session_scope() as session:
        row = await session.get(DocumentRouting, routing_id)
        if row is None or row.tenant_id != _tenant():
            raise HTTPException(status_code=404, detail="Không có đề xuất này")
        return _response(row)


@router.get("", response_model=list[RoutingResponse], dependencies=_DOC,
            summary="Danh sách đề xuất theo văn bản")
async def list_routings(document_id: str | None = Query(default=None)) -> list[RoutingResponse]:
    async with session_scope() as session:
        query = select(DocumentRouting).where(DocumentRouting.tenant_id == _tenant())
        if document_id:
            query = query.where(DocumentRouting.document_id == document_id)
        rows = (await session.execute(query.order_by(DocumentRouting.created_at.desc()))).scalars().all()
        return [_response(row) for row in rows]


async def _validate_erp_ids(primary_id: int | None, coordinating_json: str | None) -> list[int]:
    if primary_id is None:
        raise HTTPException(status_code=422, detail="Cần AiPrimaryUnitId để phê duyệt")
    try:
        coordinating = json.loads(coordinating_json or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="AiCoordinatingUnitsJson phải là JSON array") from exc
    if not isinstance(coordinating, list) or any(type(item) is not int for item in coordinating):
        raise HTTPException(status_code=422, detail="AiCoordinatingUnitsJson phải là danh sách ID số")
    try:
        async with erp_session_scope() as session:
            catalog = await ErpDonViRepository(session).list_all()
        allowed = {int(row.id) for row in catalog}
    except Exception:
        if get_settings().trust_identity_headers and not get_settings().erp_database_url:
            allowed = {int(item["id"]) for item in _DEMO_DEPARTMENTS}
        else:
            raise
    if primary_id not in allowed or any(item not in allowed for item in coordinating):
        raise HTTPException(status_code=422, detail="Đơn vị không tồn tại, đã xoá hoặc khác tenant")
    if primary_id in coordinating:
        raise HTTPException(status_code=422, detail="Đơn vị chủ trì không được là đơn vị phối hợp")
    return coordinating


@router.post("/{routing_id}/approve", response_model=RoutingResponse, dependencies=_APPROVE,
             summary="Phê duyệt đề xuất phân luồng")
async def approve(routing_id: str, request: ApproveRequest) -> RoutingResponse:
    tenant = _tenant()
    await _validate_erp_ids(request.ai_primary_unit_id, request.ai_coordinating_units_json)
    if request.urgency_level and request.urgency_level not in service.DEFAULT_URGENCY_LEVELS:
        raise HTTPException(status_code=422, detail="UrgencyLevel không hợp lệ")
    async with session_scope() as session:
        row = await session.get(DocumentRouting, routing_id)
        if row is None or row.tenant_id != tenant:
            raise HTTPException(status_code=404, detail="Không có đề xuất này")
        row.ai_primary_unit_id = request.ai_primary_unit_id
        row.ai_coordinating_units_json = request.ai_coordinating_units_json or "[]"
        if request.urgency_level:
            row.urgency_level = request.urgency_level
        row.review_note = request.review_note
        row.reviewed_by = current_principal().user_id
        row.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.updated_at = row.reviewed_at
        row.status = service.STATUS_APPROVED
        await session.flush()
        return _response(row)


@router.post("/{routing_id}/reject", response_model=RoutingResponse, dependencies=_APPROVE,
             summary="Từ chối đề xuất phân luồng")
async def reject(routing_id: str, request: ApproveRequest) -> RoutingResponse:
    async with session_scope() as session:
        row = await session.get(DocumentRouting, routing_id)
        if row is None or row.tenant_id != _tenant():
            raise HTTPException(status_code=404, detail="Không có đề xuất này")
        row.status = service.STATUS_REJECTED
        row.review_note = request.review_note
        row.reviewed_by = current_principal().user_id
        row.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.updated_at = row.reviewed_at
        await session.flush()
        return _response(row)


@router.post("/{routing_id}/rerun", response_model=RoutingResponse, dependencies=_DOC,
             summary="Chạy lại AI và tạo phiên bản đề xuất mới")
async def rerun(routing_id: str) -> RoutingResponse:
    tenant = _tenant()
    async with session_scope() as session:
        old = await session.get(DocumentRouting, routing_id)
        if old is None or old.tenant_id != tenant:
            raise HTTPException(status_code=404, detail="Không có đề xuất này")
        departments = json.loads(old.department_catalog_json or "[]")
        categories = json.loads(old.category_catalog_json or "[]")
        file_id, document_id = old.file_id, old.document_id
    row = await service.suggest(
        document_id=document_id, file_url=file_id, extracted_text="", departments=departments,
        categories=categories, urgency=["Thuong", "Khan", "Hoa toc"], tenant_id=tenant,
    )
    async with session_scope() as session:
        old = await session.get(DocumentRouting, routing_id)
        if old is not None:
            old.status = service.STATUS_SUPERSEDED
            old.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(row)
        await session.flush()
        return _response(row)