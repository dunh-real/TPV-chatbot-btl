"""Truy vấn nghiệp vụ.

Số liệu đưa vào báo cáo phải đi qua đây, không bao giờ do LLM tự sinh: LLM chỉ
nhận `dict` đã truy vấn sẵn và diễn đạt thành văn. Nhờ vậy mọi con số trong báo
cáo cuối đều đối chiếu được với dữ liệu gốc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TemplateBaoCao, VanBan


@dataclass(slots=True)
class TaiNguyenDonVi:
    """Ảnh chụp tài nguyên của một đơn vị tại thời điểm truy vấn."""

    ma_don_vi: str
    ten_don_vi: str
    quan_so: int
    quan_so_kiem_ke: date | None
    trang_bi: list[dict[str, Any]] = field(default_factory=list)
    ky: str | None = None          # None = hiện trạng; "2026-08" = số liệu kỳ đó
    ghi_chu: str = ""

    @property
    def tong_trang_bi(self) -> int:
        return sum(item["so_luong"] for item in self.trang_bi)

    def can_bao_duong(self) -> list[dict[str, Any]]:
        """Chỉ những trang bị ĐƯỢC BIẾT CHẮC là không tốt.

        `tinh_trang_tot=None` nghĩa là chưa khai báo mã trạng thái của ERP nên
        không diễn giải được - khi đó không kết luận gì, vì đoán sai theo hướng
        nào cũng ra một con số sai trong văn bản trình ký.
        """
        return [t for t in self.trang_bi if t.get("tinh_trang_tot") is False]

    @property
    def biet_tinh_trang(self) -> bool:
        return any(t.get("tinh_trang_tot") is not None for t in self.trang_bi)

    def as_dict(self) -> dict[str, Any]:
        """Dạng phẳng để nhúng vào prompt sinh báo cáo."""
        data = {
            "ma_don_vi": self.ma_don_vi,
            "ten_don_vi": self.ten_don_vi,
            "ky": self.ky,
            "quan_so": self.quan_so,
            "quan_so_kiem_ke": self.quan_so_kiem_ke.isoformat() if self.quan_so_kiem_ke else None,
            "tong_so_trang_bi": self.tong_trang_bi,
            "so_loai_trang_bi": len(self.trang_bi),
            "trang_bi": self.trang_bi,
        }
        # Vắng mặt hẳn thì prompt không nhắc tới; có mặt với giá trị 0 thì LLM sẽ
        # viết "không có trang bị nào cần bảo dưỡng" - một khẳng định chưa kiểm được.
        if self.biet_tinh_trang:
            data["so_loai_can_bao_duong"] = len(self.can_bao_duong())
        if self.ghi_chu:
            data["ghi_chu"] = self.ghi_chu
        return data


class TemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self, loai_bao_cao: str | None = None) -> list[TemplateBaoCao]:
        stmt = select(TemplateBaoCao).order_by(TemplateBaoCao.ma_template)
        if loai_bao_cao:
            stmt = stmt.where(TemplateBaoCao.loai_bao_cao == loai_bao_cao)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get(self, ma_template: str) -> TemplateBaoCao | None:
        return await self.session.get(TemplateBaoCao, ma_template)

    async def catalog(self) -> list[dict[str, str]]:
        """Chỉ tên + loại + mô tả: đủ để LLM chọn, không cần cấu trúc trường."""
        return [
            {"ma_template": t.ma_template, "ten_bao_cao": t.ten_bao_cao,
             "loai_bao_cao": t.loai_bao_cao, "mo_ta": t.mo_ta}
            for t in await self.list_all()
        ]


class VanBanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, ma_van_ban: str) -> VanBan | None:
        return await self.session.get(VanBan, ma_van_ban)

    async def get_by_doc_id(self, doc_id: str) -> VanBan | None:
        """Tra ngược từ doc_id trong Qdrant về sổ văn bản."""
        result = await self.session.execute(select(VanBan).where(VanBan.doc_id == doc_id).limit(1))
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 50, loai_van_ban: str | None = None) -> list[VanBan]:
        stmt = select(VanBan).order_by(VanBan.ngay_van_ban.desc()).limit(limit)
        if loai_van_ban:
            stmt = stmt.where(VanBan.loai_van_ban == loai_van_ban)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def upsert(self, van_ban: VanBan) -> VanBan:
        merged = await self.session.merge(van_ban)
        await self.session.flush()
        return merged

    async def link_doc_id(self, ma_van_ban: str, doc_id: str) -> None:
        """Nối văn bản với doc_id trong Qdrant để tra cứu được nội dung."""
        van_ban = await self.session.get(VanBan, ma_van_ban)
        if van_ban is not None:
            van_ban.doc_id = doc_id
            await self.session.flush()
