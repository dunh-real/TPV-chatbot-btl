"""Lớp dữ liệu nghiệp vụ: model, repository và cách số liệu đi vào báo cáo."""

from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, DonVi, PhongBan, TemplateBaoCao, TrangBi, VanBan
from app.db.repository import (
    PhongBanRepository,
    TaiNguyenRepository,
    TemplateRepository,
    VanBanRepository,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            PhongBan(ma_phong_ban="P.TCCB", ten_phong_ban="Phòng Tổ chức cán bộ",
                     email="tccb@tpv.vn", mo_ta="Quản lý nhân sự, biên chế, quân số."),
            PhongBan(ma_phong_ban="P.HCQT", ten_phong_ban="Phòng Hành chính quản trị",
                     email="hcqt@tpv.vn", mo_ta="Văn thư, lưu trữ, quản lý trang thiết bị."),
            DonVi(ma_don_vi="DV01", ten_don_vi="Đơn vị 1", quan_so=48,
                  quan_so_kiem_ke=date(2026, 8, 15)),
            TrangBi(ma_don_vi="DV01", ten_trang_bi="Máy tính để bàn", so_luong=45,
                    tinh_trang="Tốt", bao_duong_cuoi=date(2026, 6, 10)),
            TrangBi(ma_don_vi="DV01", ten_trang_bi="Máy in laser", so_luong=8,
                    tinh_trang="Cần bảo dưỡng", bao_duong_cuoi=date(2025, 11, 20)),
            TrangBi(ma_don_vi="DV01", ten_trang_bi="Máy chiếu", so_luong=4,
                    tinh_trang="Hỏng", bao_duong_cuoi=None),
            TemplateBaoCao(
                ma_template="BC_TAINGUYEN", ten_bao_cao="Báo cáo tổng hợp quân số và trang bị",
                loai_bao_cao="bao_cao_dinh_ky", mo_ta="Dùng khi cấp trên yêu cầu báo cáo...",
                truong_du_lieu=json.dumps(
                    {"sections": [{"id": "quan_so", "type": "data"},
                                  {"id": "trang_bi", "type": "table"}]},
                    ensure_ascii=False,
                ),
            ),
            VanBan(ma_van_ban="105/CV-BGĐ", ten_van_ban="Công văn báo cáo trang thiết bị",
                   loai_van_ban="cong_van_den", noi_gui="Ban Giám đốc",
                   noi_nhan="Các phòng ban", ngay_van_ban=date(2026, 9, 5),
                   file_path="data/demo/CV-105-BGD.md", dang_file="md"),
        ])
        await session.commit()
        yield session

    await engine.dispose()


# ------------------------------------------------------------ phòng ban --- #
async def test_catalog_phong_ban_co_mo_ta_cho_llm(session):
    catalog = await PhongBanRepository(session).catalog()
    assert {c["ma_phong_ban"] for c in catalog} == {"P.TCCB", "P.HCQT"}
    # Mô tả là căn cứ duy nhất để LLM định tuyến - thiếu nó thì chỉ còn đoán theo tên.
    assert all(c["mo_ta"] for c in catalog)
    assert "email" not in catalog[0]      # không đưa dữ liệu thừa vào prompt


# --------------------------------------------------------------- tài nguyên #
async def test_tai_nguyen_gop_dung_quan_so_va_trang_bi(session):
    tai_nguyen = await TaiNguyenRepository(session).get_tai_nguyen("DV01")

    assert tai_nguyen.quan_so == 48
    assert tai_nguyen.quan_so_kiem_ke == date(2026, 8, 15)
    assert tai_nguyen.tong_trang_bi == 45 + 8 + 4      # cộng theo số lượng, không đếm dòng
    assert len(tai_nguyen.trang_bi) == 3


async def test_loc_trang_bi_can_bao_duong(session):
    tai_nguyen = await TaiNguyenRepository(session).get_tai_nguyen("DV01")
    ten = {t["ten_trang_bi"] for t in tai_nguyen.can_bao_duong()}
    assert ten == {"Máy in laser", "Máy chiếu"}        # "Tốt" bị loại


async def test_dict_dua_vao_prompt_co_du_so_lieu_tong_hop(session):
    data = (await TaiNguyenRepository(session).get_tai_nguyen("DV01")).as_dict()
    assert data["tong_so_trang_bi"] == 57
    assert data["so_loai_can_bao_duong"] == 2
    assert data["quan_so_kiem_ke"] == "2026-08-15"     # JSON-safe cho prompt
    assert isinstance(data["trang_bi"], list)


async def test_don_vi_khong_ton_tai(session):
    assert await TaiNguyenRepository(session).get_tai_nguyen("KHONG_CO") is None


# --------------------------------------------------------------- template -- #
async def test_template_doc_duoc_json_fields(session):
    template = await TemplateRepository(session).get("BC_TAINGUYEN")
    assert [s["type"] for s in template.fields["sections"]] == ["data", "table"]


async def test_template_json_hong_khong_lam_vo_pipeline(session):
    template = await TemplateRepository(session).get("BC_TAINGUYEN")
    template.truong_du_lieu = "{không phải json}"
    assert template.fields == {}


async def test_catalog_template_khong_kem_cau_truc_truong(session):
    catalog = await TemplateRepository(session).catalog()
    assert catalog[0]["mo_ta"]
    assert "truong_du_lieu" not in catalog[0]    # chọn template chỉ cần mô tả


# ---------------------------------------------------------------- văn bản -- #
async def test_noi_van_ban_voi_doc_id_trong_qdrant(session):
    repo = VanBanRepository(session)
    await repo.link_doc_id("105/CV-BGĐ", "abc123def456")
    assert (await repo.get("105/CV-BGĐ")).doc_id == "abc123def456"


async def test_liet_ke_van_ban_theo_loai(session):
    ket_qua = await VanBanRepository(session).list_recent(loai_van_ban="cong_van_den")
    assert [vb.ma_van_ban for vb in ket_qua] == ["105/CV-BGĐ"]


# ------------------------------------------------------------ cấu hình ---- #
def test_cors_origins_doc_duoc_dang_csv_tu_env(tmp_path, monkeypatch):
    """CORS_ORIGINS trong .env viết dạng "a,b" chứ không phải JSON.

    pydantic-settings mặc định json.loads mọi field kiểu list trước khi validator
    chạy, nên thiếu NoDecode là app không khởi động được khi có file .env thật.
    """
    from app.core.config import Settings

    env_file = tmp_path / ".env"
    env_file.write_text("CORS_ORIGINS=http://localhost:3000,http://localhost:5173\n",
                        encoding="utf-8")
    settings = Settings(_env_file=str(env_file))

    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:5173"]


def test_env_example_nap_duoc_nguyen_ven():
    """File mẫu phải luôn nạp được - đây là thứ người dùng copy thành .env."""
    from pathlib import Path

    from app.core.config import Settings

    example = Path(__file__).resolve().parents[1] / ".env.example"
    settings = Settings(_env_file=str(example))

    assert settings.llm_model and settings.qdrant_collection
    assert settings.chunk_ideal_tokens < settings.chunk_max_tokens < settings.chunk_hard_cap
