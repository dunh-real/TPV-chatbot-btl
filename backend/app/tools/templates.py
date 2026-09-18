"""Công cụ tra mẫu báo cáo.

Mẫu là thứ quyết định văn bản sinh ra có đúng thể thức hay không, nên LLM chỉ được
CHỌN trong danh mục lấy từ CSDL chứ không được tự nghĩ ra mã mẫu. Tool này vừa tra
mẫu, vừa trả về danh sách ứng viên khi tra hụt - để agent hỏi lại người dùng thay
vì soạn nhầm loại báo cáo.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.models import TemplateBaoCao
from app.db.repository import TemplateRepository
from app.db.session import session_scope
from app.tools.base import ToolError

logger = logging.getLogger(__name__)


def _as_dict(template: TemplateBaoCao) -> dict[str, Any]:
    fields = template.fields
    meta = fields.get("meta") or {}
    return {
        "ma_template": template.ma_template,
        "ten_bao_cao": template.ten_bao_cao,
        "loai_bao_cao": template.loai_bao_cao,
        "mo_ta": template.mo_ta,
        "file_path": template.file_path,
        "sections": [
            {"id": s.get("id", ""), "title": s.get("title", ""), "type": s.get("type", "llm")}
            for s in fields.get("sections", [])
        ],
        # Trường buộc người dùng nhập (người ký, chức vụ...): agent phải hỏi trước
        # khi soạn, vì không có nguồn nào khác suy ra được.
        "required_inputs": sorted(
            key for key, spec in meta.items()
            if isinstance(spec, dict) and spec.get("source") == "input"
        ),
        "fields": fields,
    }


def _match(template: TemplateBaoCao, needle: str) -> bool:
    haystack = " ".join([template.ten_bao_cao, template.loai_bao_cao, template.mo_ta]).lower()
    return needle in haystack


async def get_template(template_type: str) -> dict[str, Any]:
    """Tra mẫu theo mã, theo loại, hoặc theo tên gọi dân dã của người dùng.

    Nhận cả ba vì người dùng gõ "BC_TONGHOP", "báo cáo tổng hợp" hay "tổng hợp quân
    số" đều đang nói tới cùng một mẫu.
    """
    needle = (template_type or "").strip()
    if not needle:
        raise ToolError("get_template: thiếu loại mẫu cần tra")

    async with session_scope() as session:
        repo = TemplateRepository(session)
        templates = await repo.list_all()
        if not templates:
            return {"found": False, "query": needle, "reason": "Chưa có mẫu nào trong CSDL",
                    "candidates": []}

        lowered = needle.lower()
        found = (
            next((t for t in templates if t.ma_template.lower() == lowered), None)
            or next((t for t in templates if t.loai_bao_cao.lower() == lowered), None)
            or next((t for t in templates if t.ten_bao_cao.lower() == lowered), None)
            or next((t for t in templates if _match(t, lowered)), None)
        )
        candidates = [
            {"ma_template": t.ma_template, "ten_bao_cao": t.ten_bao_cao,
             "loai_bao_cao": t.loai_bao_cao, "mo_ta": t.mo_ta}
            for t in templates
        ]

        if found is None:
            return {"found": False, "query": needle,
                    "reason": f"Không có mẫu nào khớp với {needle!r}",
                    "candidates": candidates}
        return {"found": True, "query": needle, **_as_dict(found), "candidates": candidates}


async def list_templates(loai_bao_cao: str | None = None) -> list[dict[str, str]]:
    """Danh mục mẫu rút gọn - đủ để LLM chọn, không kèm cấu trúc trường."""
    async with session_scope() as session:
        templates = await TemplateRepository(session).list_all(loai_bao_cao)
        return [
            {"ma_template": t.ma_template, "ten_bao_cao": t.ten_bao_cao,
             "loai_bao_cao": t.loai_bao_cao, "mo_ta": t.mo_ta}
            for t in templates
        ]
