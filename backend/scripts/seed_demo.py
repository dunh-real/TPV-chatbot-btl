#!/usr/bin/env python
"""Tạo bảng và nạp dữ liệu mẫu cho kịch bản demo.

    uv run python scripts/seed_demo.py           # SQLite theo DATABASE_URL
    uv run python scripts/seed_demo.py --reset   # xoá sạch rồi nạp lại

Kịch bản: Ban Giám đốc gửi công văn 105/CV-BGĐ yêu cầu các đơn vị báo cáo quân số
và trang thiết bị; hệ thống định tuyến, phân rã nhiệm vụ rồi sinh báo cáo từ
template + số liệu trong CSDL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (  # noqa: E402
    Base, DonVi, KiemKeTrangBi, KyKiemKe, PhongBan, TemplateBaoCao, TrangBi, VanBan,
)
from app.db.session import create_all, dispose_engine, get_engine, session_scope  # noqa: E402

PHONG_BAN = [
    PhongBan(
        ma_phong_ban="P.HCNS",
        ten_phong_ban="Phòng Hành chính nhân sự",
        email="hcns@tpv.vn",
        mo_ta=(
            "Quản lý nhân sự: tuyển dụng, hợp đồng lao động, điều động, bổ nhiệm, "
            "nâng lương, đào tạo, chế độ chính sách, bảo hiểm và hồ sơ cán bộ. "
            "Công tác văn thư, lưu trữ, tiếp nhận và phát hành văn bản. "
            "Xử lý mọi văn bản liên quan tới con người: kiểm kê nhân sự, quân số, "
            "khen thưởng, kỷ luật, nghỉ phép, chuyển tiếp hợp đồng."
        ),
    ),
    PhongBan(
        ma_phong_ban="P.KETOAN",
        ten_phong_ban="Phòng Kế toán",
        email="ketoan@tpv.vn",
        mo_ta=(
            "Hạch toán kế toán, quản lý thu chi, thanh toán, công nợ và sổ sách tài chính. "
            "Lập dự toán, quyết toán ngân sách, thẩm định kinh phí cho các đề xuất mua sắm, "
            "bảo dưỡng, thay thế trang thiết bị. Tổng hợp số liệu tài chính toàn công ty."
        ),
    ),
    PhongBan(
        ma_phong_ban="P.HCC",
        ten_phong_ban="Phòng Hành chính công",
        email="hcc@tpv.vn",
        mo_ta=(
            "Quan hệ với cơ quan nhà nước, thủ tục hành chính công, giấy phép, hồ sơ pháp lý. "
            "Theo dõi việc chấp hành văn bản chỉ đạo của cấp trên và báo cáo ra bên ngoài. "
            "Quản lý con dấu, chứng thực và các thủ tục hành chính đối ngoại."
        ),
    ),
    PhongBan(
        ma_phong_ban="P.KYTHUAT",
        ten_phong_ban="Phòng Kỹ thuật",
        email="kythuat@tpv.vn",
        mo_ta=(
            "Quản lý kỹ thuật, vận hành, bảo dưỡng, sửa chữa máy móc, trang thiết bị và "
            "hệ thống công nghệ thông tin. Kiểm kê và đánh giá tình trạng kỹ thuật của "
            "tài sản, thiết bị; lập kế hoạch bảo dưỡng định kỳ và tham mưu phương án "
            "nâng cấp, thay thế thiết bị."
        ),
    ),
    PhongBan(
        ma_phong_ban="P.KINHDOANH",
        ten_phong_ban="Phòng Kinh doanh",
        email="kinhdoanh@tpv.vn",
        mo_ta=(
            "Phát triển thị trường, chăm sóc khách hàng, hợp đồng bán hàng và doanh thu. "
            "Quản lý trang thiết bị, phương tiện phục vụ bán hàng và nhân sự kinh doanh "
            "thuộc phạm vi phòng. Báo cáo tình hình kinh doanh theo yêu cầu của Ban Giám đốc."
        ),
    ),
]

TEMPLATE_TAI_NGUYEN = {
    "meta": {
        "noi_gui": {"source": "unit.ten_don_vi"},
        "noi_nhan": {"source": "literal", "value": "Ban Giám đốc (qua Phòng Hành chính nhân sự)"},
        "nguoi_ky": {"source": "input", "label": "Tên người ký"},
        "chuc_vu_ky": {"source": "input", "label": "Chức vụ người ký"},
        "ngay_bao_cao": {"source": "today"},
        "can_cu": {"source": "input", "label": "Văn bản căn cứ"},
    },
    "sections": [
        {
            "id": "quan_so",
            "title": "I. TÌNH HÌNH QUÂN SỐ",
            "type": "data",
            "query": "don_vi",
            "fields": ["quan_so", "quan_so_kiem_ke"],
            "narrative": "Nêu quân số hiện có và ngày kiểm kê gần nhất. "
                         "Nếu kiểm kê quá 6 tháng thì nêu rõ là đã quá hạn.",
        },
        {
            "id": "trang_bi",
            "title": "II. TÌNH TRẠNG TRANG THIẾT BỊ",
            "type": "table",
            "query": "trang_bi",
            "columns": ["ten_trang_bi", "so_luong", "tinh_trang", "cap_nhat_cuoi"],
            # Không hỏi "số loại đang cần bảo dưỡng": ERP không có chỉ tiêu đó.
            # Mẫu hỏi một chỉ tiêu không tồn tại thì model sẽ dựng ra một con số
            # để trả lời - và con số đó truy được về dữ liệu nên van chắn không
            # bắt. Ba lần chạy thật đều ra "có 5 loại đang cần bảo dưỡng".
            "narrative": "Sau bảng, nêu tổng số lượng trang bị và số chủng loại. "
                         "Không nhận xét về tình trạng nếu bảng chưa có nhãn tình trạng.",
        },
        {
            "id": "de_xuat",
            "title": "III. ĐỀ XUẤT, KIẾN NGHỊ",
            "type": "llm",
            "narrative": "Nêu kiến nghị rà soát dựa trên số lượng và chủng loại đã nêu ở "
                         "mục II. Không suy ra tình trạng, hạn bảo dưỡng hay nhu cầu "
                         "thay thế của bất kỳ trang bị nào - số liệu không có những "
                         "thông tin đó.",
        },
    ],
}

TEMPLATE_QUAN_SO = {
    "meta": {
        "noi_gui": {"source": "unit.ten_don_vi"},
        "noi_nhan": {"source": "literal", "value": "Phòng Hành chính nhân sự"},
        "nguoi_ky": {"source": "input", "label": "Tên người ký"},
        "ngay_bao_cao": {"source": "today"},
    },
    "sections": [
        {"id": "quan_so", "title": "I. QUÂN SỐ HIỆN CÓ", "type": "data",
         "query": "don_vi", "fields": ["quan_so", "quan_so_kiem_ke"],
         "narrative": "Nêu quân số và thời điểm kiểm kê."},
        {"id": "kien_nghi", "title": "II. KIẾN NGHỊ", "type": "llm",
         "narrative": "Kiến nghị về biên chế nếu cần."},
    ],
}

TEMPLATE_TONG_HOP = {
    "meta": {
        "noi_gui": {"source": "literal", "value": "PHÒNG HÀNH CHÍNH QUẢN TRỊ"},
        "noi_nhan": {"source": "literal", "value": "Ban Giám đốc"},
        "nguoi_ky": {"source": "input", "label": "Tên người ký"},
        "chuc_vu_ky": {"source": "input", "label": "Chức vụ người ký"},
        "ngay_bao_cao": {"source": "today"},
    },
    # Các mục của báo cáo tổng hợp do workflow 4 dựng bằng code (có biểu đồ, có
    # bảng phân theo đơn vị) nên không khai báo tĩnh ở đây.
    "sections": [],
}

TEMPLATES = [
    TemplateBaoCao(
        ma_template="BC_TAINGUYEN",
        ten_bao_cao="Báo cáo tổng hợp quân số và trang thiết bị",
        loai_bao_cao="bao_cao_dinh_ky",
        mo_ta=(
            "Dùng khi cấp trên yêu cầu báo cáo đồng thời về quân số và tình trạng trang "
            "thiết bị của đơn vị, thường phục vụ xây dựng kế hoạch bổ sung trang bị hoặc "
            "điều chỉnh biên chế. Gồm ba phần: quân số, bảng trang thiết bị, và đề xuất."
        ),
        file_path="data/templates/bao_cao_tai_nguyen.docx",
        truong_du_lieu=json.dumps(TEMPLATE_TAI_NGUYEN, ensure_ascii=False),
    ),
    TemplateBaoCao(
        ma_template="BC_QUANSO",
        ten_bao_cao="Báo cáo quân số",
        loai_bao_cao="bao_cao_dinh_ky",
        mo_ta=(
            "Dùng khi chỉ cần báo cáo về quân số, biên chế, không đề cập trang thiết bị. "
            "Thường gửi Phòng Hành chính nhân sự theo định kỳ tháng hoặc quý."
        ),
        truong_du_lieu=json.dumps(TEMPLATE_QUAN_SO, ensure_ascii=False),
    ),
    TemplateBaoCao(
        ma_template="BC_TONGHOP",
        ten_bao_cao="Báo cáo tổng hợp quân số và trang thiết bị toàn cơ quan",
        loai_bao_cao="bao_cao_tong_hop",
        mo_ta=(
            "Dùng khi cần tổng hợp số liệu của NHIỀU đơn vị trong một kỳ, có so sánh "
            "với kỳ trước và có biểu đồ. Thường do Phòng Hành chính nhân sự lập để "
            "gửi Ban Giám đốc sau khi các đơn vị đã gửi báo cáo."
        ),
        file_path="data/templates/bao_cao_tai_nguyen.docx",
        truong_du_lieu=json.dumps(TEMPLATE_TONG_HOP, ensure_ascii=False),
    ),
]

DON_VI = [
    (DonVi(ma_don_vi="DV01", ten_don_vi="Đơn vị 1 - Khối văn phòng",
           quan_so=48, quan_so_kiem_ke=date(2026, 8, 15)),
     [("Máy tính để bàn", 45, "Tốt", date(2026, 6, 10)),
      ("Máy in laser", 8, "Cần bảo dưỡng", date(2025, 11, 20)),
      ("Máy photocopy", 3, "Tốt", date(2026, 7, 1)),
      ("Máy chiếu", 4, "Hỏng", date(2025, 3, 5))]),
    (DonVi(ma_don_vi="DV02", ten_don_vi="Đơn vị 2 - Khối kỹ thuật",
           quan_so=65, quan_so_kiem_ke=date(2026, 3, 1)),
     [("Máy tính trạm", 60, "Tốt", date(2026, 5, 12)),
      ("Máy chủ", 6, "Tốt", date(2026, 8, 2)),
      ("Thiết bị đo kiểm", 12, "Cần bảo dưỡng", date(2025, 9, 18)),
      ("Xe công vụ", 2, "Cần bảo dưỡng", date(2026, 1, 25))]),
    (DonVi(ma_don_vi="DV03", ten_don_vi="Đơn vị 3 - Khối hậu cần",
           quan_so=32, quan_so_kiem_ke=date(2026, 9, 1)),
     [("Xe tải nhẹ", 4, "Tốt", date(2026, 6, 30)),
      ("Kho lạnh", 2, "Hỏng", date(2024, 12, 15)),
      ("Máy phát điện", 3, "Tốt", date(2026, 4, 8))]),
]

# (kỳ, ngày kiểm kê, quân số, (có mặt, đi học, nghỉ phép),
#  [(tên trang bị, số lượng, tình trạng, bảo dưỡng cuối)])
KIEM_KE: dict[str, list[tuple]] = {
    "DV01": [
        ("2026-06", date(2026, 6, 28), 45, (42, 2, 1), [
            ("Máy tính để bàn", 42, "Tốt", date(2026, 6, 10)),
            ("Máy in laser", 8, "Tốt", date(2026, 5, 20)),
            ("Máy photocopy", 3, "Tốt", date(2026, 4, 1)),
            ("Máy chiếu", 4, "Cần bảo dưỡng", date(2025, 3, 5))]),
        ("2026-07", date(2026, 7, 30), 46, (43, 2, 1), [
            ("Máy tính để bàn", 44, "Tốt", date(2026, 6, 10)),
            ("Máy in laser", 8, "Cần bảo dưỡng", date(2025, 11, 20)),
            ("Máy photocopy", 3, "Tốt", date(2026, 7, 1)),
            ("Máy chiếu", 4, "Cần bảo dưỡng", date(2025, 3, 5))]),
        ("2026-08", date(2026, 8, 29), 48, (44, 3, 1), [
            ("Máy tính để bàn", 45, "Tốt", date(2026, 6, 10)),
            ("Máy in laser", 8, "Cần bảo dưỡng", date(2025, 11, 20)),
            ("Máy photocopy", 3, "Tốt", date(2026, 7, 1)),
            ("Máy chiếu", 4, "Hỏng", date(2025, 3, 5))]),
        ("2026-09", date(2026, 9, 27), 48, (45, 2, 1), [
            ("Máy tính để bàn", 45, "Tốt", date(2026, 9, 5)),
            ("Máy in laser", 8, "Cần bảo dưỡng", date(2025, 11, 20)),
            ("Máy photocopy", 3, "Tốt", date(2026, 7, 1)),
            ("Máy chiếu", 4, "Hỏng", date(2025, 3, 5))]),
    ],
    "DV02": [
        ("2026-06", date(2026, 6, 25), 62, (57, 3, 2), [
            ("Máy tính trạm", 58, "Tốt", date(2026, 5, 12)),
            ("Máy chủ", 6, "Tốt", date(2026, 2, 2)),
            ("Thiết bị đo kiểm", 12, "Tốt", date(2026, 3, 18)),
            ("Xe công vụ", 2, "Tốt", date(2026, 1, 25))]),
        ("2026-07", date(2026, 7, 28), 64, (59, 3, 2), [
            ("Máy tính trạm", 60, "Tốt", date(2026, 5, 12)),
            ("Máy chủ", 6, "Tốt", date(2026, 2, 2)),
            ("Thiết bị đo kiểm", 12, "Cần bảo dưỡng", date(2025, 9, 18)),
            ("Xe công vụ", 2, "Tốt", date(2026, 1, 25))]),
        ("2026-08", date(2026, 8, 30), 65, (60, 3, 2), [
            ("Máy tính trạm", 60, "Tốt", date(2026, 5, 12)),
            ("Máy chủ", 6, "Tốt", date(2026, 8, 2)),
            ("Thiết bị đo kiểm", 12, "Cần bảo dưỡng", date(2025, 9, 18)),
            ("Xe công vụ", 2, "Cần bảo dưỡng", date(2026, 1, 25))]),
        ("2026-09", date(2026, 9, 26), 65, (61, 2, 2), [
            ("Máy tính trạm", 61, "Tốt", date(2026, 9, 12)),
            ("Máy chủ", 6, "Tốt", date(2026, 8, 2)),
            ("Thiết bị đo kiểm", 12, "Cần bảo dưỡng", date(2025, 9, 18)),
            ("Xe công vụ", 2, "Cần bảo dưỡng", date(2026, 1, 25))]),
    ],
    "DV03": [
        ("2026-07", date(2026, 7, 29), 30, (28, 1, 1), [
            ("Xe tải nhẹ", 4, "Tốt", date(2026, 6, 30)),
            ("Kho lạnh", 2, "Cần bảo dưỡng", date(2024, 12, 15)),
            ("Máy phát điện", 3, "Tốt", date(2026, 4, 8))]),
        ("2026-08", date(2026, 8, 31), 31, (29, 1, 1), [
            ("Xe tải nhẹ", 4, "Tốt", date(2026, 6, 30)),
            ("Kho lạnh", 2, "Hỏng", date(2024, 12, 15)),
            ("Máy phát điện", 3, "Tốt", date(2026, 4, 8))]),
        ("2026-09", date(2026, 9, 28), 32, (30, 1, 1), [
            ("Xe tải nhẹ", 4, "Tốt", date(2026, 9, 2)),
            ("Kho lạnh", 2, "Hỏng", date(2024, 12, 15)),
            ("Máy phát điện", 3, "Tốt", date(2026, 4, 8))]),
    ],
}

VAN_BAN = VanBan(
    ma_van_ban="105/CV-BGĐ",
    ten_van_ban="Công văn về việc tổng hợp, báo cáo tình trạng trang thiết bị và quân số",
    loai_van_ban="cong_van_den",
    noi_gui="Ban Giám đốc",
    noi_nhan="Các phòng ban, đơn vị trực thuộc",
    mo_ta="Yêu cầu rà soát quân số, thống kê trang thiết bị và đề xuất bổ sung cho năm 2027. "
          "Hạn báo cáo: 20/9/2026, gửi về Phòng Hành chính nhân sự.",
    ngay_van_ban=date(2026, 9, 5),
    file_path="data/demo/CV-105-BGD.md",
    dang_file="md",
)


async def main(reset: bool) -> int:
    if reset:
        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        print("Đã xoá toàn bộ bảng cũ.")

    await create_all()

    async with session_scope() as session:
        # Danh mục phòng ban là nguồn cho bước phân công văn bản, nên seed phải là
        # nguồn sự thật: `merge` không xoá dòng cũ, để lại thì model vẫn phân việc
        # cho những phòng ban đã bỏ.
        from sqlalchemy import delete

        await session.execute(
            delete(PhongBan).where(
                PhongBan.ma_phong_ban.notin_([pb.ma_phong_ban for pb in PHONG_BAN])
            )
        )
        for phong_ban in PHONG_BAN:
            await session.merge(phong_ban)
        for template in TEMPLATES:
            await session.merge(template)
        for don_vi, danh_sach in DON_VI:
            await session.merge(don_vi)
            for ten, so_luong, tinh_trang, bao_duong in danh_sach:
                await session.merge(
                    TrangBi(ma_don_vi=don_vi.ma_don_vi, ten_trang_bi=ten, so_luong=so_luong,
                            tinh_trang=tinh_trang, cap_nhat_cuoi=bao_duong)
                )
        await session.merge(VAN_BAN)
        await session.flush()

        # Kỳ kiểm kê: xoá kỳ cũ của đơn vị rồi ghi lại, tránh nhân đôi khi chạy nhiều lần.
        from sqlalchemy import delete, select

        so_ky = 0
        for ma_don_vi, cac_ky in KIEM_KE.items():
            for ky, ngay, quan_so, (co_mat, di_hoc, nghi_phep), danh_sach in cac_ky:
                existing = await session.execute(
                    select(KyKiemKe).where(KyKiemKe.ma_don_vi == ma_don_vi, KyKiemKe.ky == ky)
                )
                if (cu := existing.scalar_one_or_none()) is not None:
                    await session.execute(
                        delete(KiemKeTrangBi).where(KiemKeTrangBi.kiem_ke_id == cu.id)
                    )
                    await session.delete(cu)
                    await session.flush()

                kiem_ke = KyKiemKe(ma_don_vi=ma_don_vi, ky=ky, ngay_kiem_ke=ngay,
                                   quan_so=quan_so, co_mat=co_mat, vang=quan_so - co_mat,
                                   di_hoc=di_hoc, nghi_phep=nghi_phep)
                session.add(kiem_ke)
                await session.flush()
                for ten, so_luong, tinh_trang, bao_duong in danh_sach:
                    session.add(KiemKeTrangBi(
                        kiem_ke_id=kiem_ke.id, ten_trang_bi=ten, so_luong=so_luong,
                        tinh_trang=tinh_trang, cap_nhat_cuoi=bao_duong))
                so_ky += 1

    print(f"Đã nạp: {len(PHONG_BAN)} phòng ban, {len(TEMPLATES)} template, "
          f"{len(DON_VI)} đơn vị, {so_ky} kỳ kiểm kê, 1 văn bản mẫu.")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nạp dữ liệu mẫu cho demo")
    parser.add_argument("--reset", action="store_true", help="Xoá sạch bảng rồi nạp lại")
    ns = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(main(ns.reset)))
