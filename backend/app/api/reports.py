"""API workflow 3: soạn văn bản theo mẫu."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.agents.graph import run_aggregate_workflow, run_draft_workflow
from app.api.files import output_file_response
from app.db.repository import TemplateRepository
from app.schemas.reports import (
    AggregateRequest,
    AggregateResponse,
    DraftRequest,
    DraftResponse,
    TemplateSummary,
)

from app.agents import quyen
from app.schemas.templates import TemplateAnalyzeRequest, TemplateConfirmRequest, TemplatePreviewRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/templates", response_model=list[TemplateSummary], summary="Danh sách mẫu báo cáo")
async def list_templates() -> list[TemplateSummary]:
    return [TemplateSummary(**item) for item in await TemplateRepository().catalog()]


@router.get("/templates/{template_id}/versions/{version_no}",
            dependencies=[Depends(quyen.can_quyen("Ai.ReportDrafting", "đọc mẫu báo cáo"))])
async def template_version(template_id: int, version_no: int):
    from app.services.ai_templates import get_version, binding
    template = await get_version(template_id, version_no)
    mapping = binding(template)
    return dict(template_id=template.ai_id, template_version=template.ai_version,
        template=template.ma_template, template_name=template.ten_bao_cao,
        mapping=mapping.model_dump(), required_inputs=[f.model_dump() for f in mapping.fields
            if f.kind=='input' and f.input_key not in {'ky','ky_truoc'}],
        requires_period=any('{{ky' in f.model_dump_json() or f.input_key in {'ky','ky_truoc'} for f in mapping.fields))


@router.post("/templates/analyze", dependencies=[Depends(quyen.can_quyen("Ai.ReportDrafting", "phân tích mẫu"))])
async def analyze_template(request: TemplateAnalyzeRequest):
    from app.services.template_analyzer import analyze
    return await analyze(request.file_id)


@router.post("/templates/confirm", dependencies=[Depends(quyen.can_quyen("Ai.ReportDrafting", "xác nhận mẫu"))])
async def confirm_template(request: TemplateConfirmRequest):
    from app.services.template_analyzer import confirm
    return await confirm(request.file_id, request.mapping, store_file=True)


@router.post("/templates/preview", response_model=DraftResponse,
             dependencies=[Depends(quyen.can_quyen("Ai.ReportDrafting", "xem thử báo cáo theo mẫu"))])
async def preview_template(request: TemplatePreviewRequest):
    """Bản nháp thử từ mẫu upload: không tạo mẫu, tác vụ hoặc sản phẩm MSSQL."""
    from app.db.business_types import TemplateInfo
    from app.services.template_analyzer import confirm, template_path
    from app.services.report_fields import draft_from_mapping
    confirmed = await confirm(request.file_id, request.mapping)
    template = TemplateInfo(ma_template="PREVIEW", ten_bao_cao="Bản nháp theo mẫu tải lên",
        file_path=str(template_path(request.file_id)), fields=confirmed["structure_json"])
    result = await draft_from_mapping(request.request, None, None, request.inputs,
        request.nguon, request.source_file_id, request.doc_ids, None, [], template=template,
        period=request.period, content=request.content)
    if result.get("output_path"):
        result["download_url"] = f"/api/reports/download/{quote(Path(result['output_path']).name)}"
    return DraftResponse(**result)


@router.post("/draft", response_model=DraftResponse, summary="Soạn báo cáo theo mẫu",
             dependencies=[Depends(quyen.can_quyen("Ai.ReportDrafting", "soạn văn bản"))])
async def draft(request: DraftRequest) -> DraftResponse:
    """Soạn một báo cáo từ MỘT nguồn. `nguon` chọn nguồn đó là gì.

    - `csdl` (mặc định): discovery sinh SQL chỉ đọc từ câu hỏi; Word dựng bảng từ
      QueryResult có cột/dòng động.
    - `tai_lieu`: đọc một file đã tải lên (`file_id`) rồi soạn báo cáo từ chính
      nội dung đó. Bảo đảm yếu hơn - chỉ chặn được số KHÔNG có trong tài liệu,
      không chặn được số có thật nhưng dùng sai chỗ.

    Cả hai nhánh: con số nào trong phần LLM viết mà không truy được về nguồn thì
    hệ thống cho viết lại; vẫn sai thì không xuất file và trả
    `validation.status=failed`.
    """
    result = await run_draft_workflow(
        request=request.request, ma_don_vi=request.ma_don_vi, inputs=request.inputs,
        nguon=request.nguon, file_id=request.file_id,
        template_id=request.template_id, template_version=request.template_version, doc_ids=request.doc_ids,
        period=request.period, content=request.content,
    )

    if result.get("output_path"):
        result["download_url"] = f"/api/reports/download/{quote(Path(result['output_path']).name)}"
    return DraftResponse(**result)


@router.post("/aggregate", response_model=AggregateResponse,
             summary="Tổng hợp báo cáo nhiều đơn vị (workflow 4)",
             dependencies=[Depends(quyen.can_quyen("Ai.ReportSummary.Create", "tổng hợp báo cáo"))])
async def aggregate(request: AggregateRequest) -> AggregateResponse:
    """Nhánh ERP dựng bảng từ discovery; nhánh tài liệu tổng hợp các báo cáo đã nộp.

    Trường `data` trả về nguyên kết quả của các data tool: mọi con số trong báo cáo
    đều đối chiếu được với nó.
    """
    result = await run_aggregate_workflow(request.request, inputs=request.inputs)
    if result.get("output_path"):
        result["download_url"] = f"/api/reports/download/{quote(Path(result['output_path']).name)}"
    return AggregateResponse(**result)


@router.get("/download/{filename}", summary="Tải file báo cáo đã sinh")
async def download(filename: str) -> FileResponse:
    # Chặn cả vượt thư mục, đọc chéo thuê bao, lẫn bản cũ trong cache trình duyệt.
    return await output_file_response(filename)
