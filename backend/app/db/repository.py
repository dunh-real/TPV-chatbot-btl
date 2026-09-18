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

from app.db.models import DonVi, KiemKeTrangBi, KyKiemKe, PhongBan, TemplateBaoCao, TrangBi, VanBan


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
        return [t for t in self.trang_bi if t["tinh_trang"].lower() not in ("tốt", "tot", "good")]

    def as_dict(self) -> dict[str, Any]:
        """Dạng phẳng để nhúng vào prompt sinh báo cáo."""
        return {
            "ma_don_vi": self.ma_don_vi,
            "ten_don_vi": self.ten_don_vi,
            "ky": self.ky,
            "quan_so": self.quan_so,
            "quan_so_kiem_ke": self.quan_so_kiem_ke.isoformat() if self.quan_so_kiem_ke else None,
            "tong_so_trang_bi": self.tong_trang_bi,
            "so_loai_trang_bi": len(self.trang_bi),
            "so_loai_can_bao_duong": len(self.can_bao_duong()),
            "trang_bi": self.trang_bi,
        }


class PhongBanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[PhongBan]:
        result = await self.session.execute(select(PhongBan).order_by(PhongBan.ma_phong_ban))
        return list(result.scalars())

    async def get(self, ma_phong_ban: str) -> PhongBan | None:
        return await self.session.get(PhongBan, ma_phong_ban)

    async def catalog(self) -> list[dict[str, str]]:
        """Danh mục rút gọn đưa vào prompt phân loại/định tuyến."""
        return [pb.as_catalog_entry() for pb in await self.list_all()]


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


class TaiNguyenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_don_vi(self) -> list[DonVi]:
        result = await self.session.execute(select(DonVi).order_by(DonVi.ma_don_vi))
        return list(result.scalars())

    async def get_tai_nguyen(self, ma_don_vi: str) -> TaiNguyenDonVi | None:
        don_vi = await self.session.get(DonVi, ma_don_vi)
        if don_vi is None:
            return None

        result = await self.session.execute(
            select(TrangBi).where(TrangBi.ma_don_vi == ma_don_vi).order_by(TrangBi.ten_trang_bi)
        )
        trang_bi = [
            {
                "ten_trang_bi": tb.ten_trang_bi,
                "so_luong": tb.so_luong,
                "tinh_trang": tb.tinh_trang,
                "bao_duong_cuoi": tb.bao_duong_cuoi.isoformat() if tb.bao_duong_cuoi else None,
            }
            for tb in result.scalars()
        ]
        return TaiNguyenDonVi(
            ma_don_vi=don_vi.ma_don_vi,
            ten_don_vi=don_vi.ten_don_vi,
            quan_so=don_vi.quan_so,
            quan_so_kiem_ke=don_vi.quan_so_kiem_ke,
            trang_bi=trang_bi,
        )


class KiemKeRepository:
    """Số liệu theo kỳ - nguồn cho các báo cáo có mốc thời gian."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_ky(self, ma_don_vi: str | None = None) -> list[str]:
        stmt = select(KyKiemKe.ky).distinct().order_by(KyKiemKe.ky.desc())
        if ma_don_vi:
            stmt = stmt.where(KyKiemKe.ma_don_vi == ma_don_vi)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_ky_gan_nhat(self, ma_don_vi: str, den_ky: str | None = None) -> str | None:
        """Kỳ mới nhất không muộn hơn `den_ky` - dùng khi kỳ yêu cầu chưa kiểm kê."""
        stmt = select(KyKiemKe.ky).where(KyKiemKe.ma_don_vi == ma_don_vi)
        if den_ky:
            stmt = stmt.where(KyKiemKe.ky <= den_ky)
        stmt = stmt.order_by(KyKiemKe.ky.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_tai_nguyen_theo_ky(self, ma_don_vi: str, ky: str) -> TaiNguyenDonVi | None:
        don_vi = await self.session.get(DonVi, ma_don_vi)
        if don_vi is None:
            return None

        result = await self.session.execute(
            select(KyKiemKe).where(KyKiemKe.ma_don_vi == ma_don_vi, KyKiemKe.ky == ky)
        )
        kiem_ke = result.scalar_one_or_none()
        if kiem_ke is None:
            return None

        rows = await self.session.execute(
            select(KiemKeTrangBi)
            .where(KiemKeTrangBi.kiem_ke_id == kiem_ke.id)
            .order_by(KiemKeTrangBi.ten_trang_bi)
        )
        trang_bi = [
            {
                "ten_trang_bi": tb.ten_trang_bi,
                "so_luong": tb.so_luong,
                "tinh_trang": tb.tinh_trang,
                "bao_duong_cuoi": tb.bao_duong_cuoi.isoformat() if tb.bao_duong_cuoi else None,
            }
            for tb in rows.scalars()
        ]
        return TaiNguyenDonVi(
            ma_don_vi=don_vi.ma_don_vi,
            ten_don_vi=don_vi.ten_don_vi,
            quan_so=kiem_ke.quan_so,
            quan_so_kiem_ke=kiem_ke.ngay_kiem_ke,
            trang_bi=trang_bi,
            ky=ky,
            ghi_chu=kiem_ke.ghi_chu,
        )


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
