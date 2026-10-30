"""Truy vấn nghiệp vụ.

Số liệu đưa vào báo cáo phải đi qua đây, không bao giờ do LLM tự sinh: LLM chỉ
nhận `dict` đã truy vấn sẵn và diễn đạt thành văn. Nhờ vậy mọi con số trong báo
cáo cuối đều đối chiếu được với dữ liệu gốc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


from app.db.business_types import DocumentInfo


@dataclass(slots=True)
class TaiNguyenDonVi:
    """Ảnh chụp tài nguyên của một đơn vị tại thời điểm truy vấn."""

    ma_don_vi: str
    ten_don_vi: str
    nhan_su: int
    nhan_su_kiem_ke: date | None
    thiet_bi: list[dict[str, Any]] = field(default_factory=list)
    ky: str | None = None          # None = hiện trạng; "2026-08" = số liệu kỳ đó
    ghi_chu: str = ""

    @property
    def tong_thiet_bi(self) -> int:
        return sum(item["so_luong"] for item in self.thiet_bi)

    def can_bao_duong(self) -> list[dict[str, Any]]:
        """Chỉ những thiết bị ĐƯỢC BIẾT CHẮC là không tốt.

        `tinh_trang_tot=None` nghĩa là chưa khai báo mã trạng thái của ERP nên
        không diễn giải được - khi đó không kết luận gì, vì đoán sai theo hướng
        nào cũng ra một con số sai trong văn bản trình ký.
        """
        return [t for t in self.thiet_bi if t.get("tinh_trang_tot") is False]

    @property
    def biet_tinh_trang(self) -> bool:
        return any(t.get("tinh_trang_tot") is not None for t in self.thiet_bi)

    def as_dict(self) -> dict[str, Any]:
        """Dạng phẳng để nhúng vào prompt sinh báo cáo."""
        data = {
            "ma_don_vi": self.ma_don_vi,
            "ten_don_vi": self.ten_don_vi,
            "ky": self.ky,
            "nhan_su": self.nhan_su,
            "nhan_su_kiem_ke": self.nhan_su_kiem_ke.isoformat() if self.nhan_su_kiem_ke else None,
            "tong_so_thiet_bi": self.tong_thiet_bi,
            "so_loai_thiet_bi": len(self.thiet_bi),
            "thiet_bi": self.thiet_bi,
        }
        # Vắng mặt hẳn thì prompt không nhắc tới; có mặt với giá trị 0 thì LLM sẽ
        # viết "không có thiết bị nào cần bảo dưỡng" - một khẳng định chưa kiểm được.
        if self.biet_tinh_trang:
            data["so_loai_can_bao_duong"] = len(self.can_bao_duong())
        if self.ghi_chu:
            data["ghi_chu"] = self.ghi_chu
        return data


class TemplateRepository:
    """Mẫu nghiệp vụ luôn đọc từ MSSQL, không dùng session pháp lý."""

    async def list_all(self, loai_bao_cao=None):
        from app.services.ai_templates import list_templates
        return await list_templates(loai_bao_cao)

    async def get(self, ma_template):
        from app.services.ai_templates import materialize
        template = next((t for t in await self.list_all() if t.ma_template == ma_template), None)
        return await materialize(template) if template else None

    async def catalog(self):
        return [{"ma_template": t.ma_template, "ten_bao_cao": t.ten_bao_cao,
                 "loai_bao_cao": t.loai_bao_cao, "mo_ta": t.mo_ta}
                for t in await self.list_all()]


class DocumentRepository:
    """Tra tài liệu nguồn trong MSSQL; báo cáo đầu ra có bảng sản phẩm riêng."""

    async def get(self, key):
        from sqlalchemy import or_
        from app.db.ai_session import repository
        async with repository() as repo:
            table = repo.table("KnowledgeDocuments")
            rows = await repo.rows("KnowledgeDocuments", or_(table.c.DocumentNumber == key,
                table.c.DocCode == key, table.c.AiServerDocId == key), owned=True)
            if not rows:
                return None
            if len(rows) != 1:
                raise ValueError("Định danh văn bản không duy nhất")
            row = rows[0]
            return DocumentInfo(ma_van_ban=row["DocCode"] or row["DocumentNumber"],
                ten_van_ban=row["Title"], noi_gui=row["IssuingAgency"] or "",
                ngay_van_ban=row["IssuedDate"], doc_id=row["AiServerDocId"])

    async def get_by_doc_id(self, doc_id):
        return await self.get(doc_id)
