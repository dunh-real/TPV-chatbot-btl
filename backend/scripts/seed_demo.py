#!/usr/bin/env python
"""Nạp dữ liệu mẫu cho máy cài mới.

    uv run python scripts/seed_demo.py           # nạp/cập nhật mẫu báo cáo + văn bản demo
    uv run python scripts/seed_demo.py --reset   # xoá đúng những dòng script này tạo, rồi nạp lại

CHỈ nạp hai bảng mà hệ thống này THỰC SỰ SỞ HỮU: `template_bao_cao` và
`van_ban`. Phòng ban, đơn vị, nhân sự, thiết bị **không** còn ở đây - chúng nằm
trong ERP và chỉ được ĐỌC (`app.db.erp_models`). Bản cũ của script này seed cả
`DonVi`/`TrangBi`/`KyKiemKe`; các model đó đã bị xoá từ đợt chuyển sang ERP nên
script chết ngay ở dòng `import`.

Không có đường nào để script này dựng dữ liệu nghiệp vụ nữa, và cũng không nên
có: ghi vào ERP là việc của ERP. Máy cài mới muốn có số liệu thì trỏ
`ERP_DATABASE_URL` vào ERP thật.
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

from sqlalchemy import delete  # noqa: E402

from app.db.models import TemplateBaoCao, VanBan  # noqa: E402
from app.db.session import create_all, dispose_engine, session_scope  # noqa: E402

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
            "id": "nhan_su",
            "title": "I. TÌNH HÌNH NHÂN SỰ",
            "type": "data",
            "query": "don_vi",
            "fields": ["nhan_su", "nhan_su_kiem_ke"],
            "narrative": "Nêu nhân sự hiện có và ngày kiểm kê gần nhất. "
                         "Nếu kiểm kê quá 6 tháng thì nêu rõ là đã quá hạn.",
        },
        {
            "id": "thiet_bi",
            "title": "II. TÌNH TRẠNG TRANG THIẾT BỊ",
            "type": "table",
            "query": "thiet_bi",
            "columns": ["ten_thiet_bi", "so_luong", "tinh_trang", "cap_nhat_cuoi"],
            # Không hỏi "số loại đang cần bảo dưỡng": ERP không có chỉ tiêu đó.
            # Mẫu hỏi một chỉ tiêu không tồn tại thì model sẽ dựng ra một con số
            # để trả lời - và con số đó truy được về dữ liệu nên van chắn không
            # bắt. Ba lần chạy thật đều ra "có 5 loại đang cần bảo dưỡng".
            "narrative": "Sau bảng, nêu tổng số lượng thiết bị và số chủng loại. "
                         "Không nhận xét về tình trạng nếu bảng chưa có nhãn tình trạng.",
        },
        {
            "id": "de_xuat",
            "title": "III. ĐỀ XUẤT, KIẾN NGHỊ",
            "type": "llm",
            "narrative": "Nêu kiến nghị rà soát dựa trên số lượng và chủng loại đã nêu ở "
                         "mục II. Không suy ra tình trạng, hạn bảo dưỡng hay nhu cầu "
                         "thay thế của bất kỳ thiết bị nào - số liệu không có những "
                         "thông tin đó.",
        },
    ],
}

TEMPLATE_NHAN_SU = {
    "meta": {
        "noi_gui": {"source": "unit.ten_don_vi"},
        "noi_nhan": {"source": "literal", "value": "Phòng Hành chính nhân sự"},
        "nguoi_ky": {"source": "input", "label": "Tên người ký"},
        "ngay_bao_cao": {"source": "today"},
    },
    "sections": [
        {"id": "nhan_su", "title": "I. NHÂN SỰ HIỆN CÓ", "type": "data",
         "query": "don_vi", "fields": ["nhan_su", "nhan_su_kiem_ke"],
         "narrative": "Nêu nhân sự và thời điểm kiểm kê."},
        {"id": "kien_nghi", "title": "II. KIẾN NGHỊ", "type": "llm",
         "narrative": "Kiến nghị về biên chế nếu cần."},
    ],
}

# Đối xứng với TEMPLATE_NHAN_SU: có mẫu chỉ về người thì phải có mẫu chỉ về đồ.
# Thiếu nó, câu "soạn báo cáo thiết bị cho Phòng X" không khớp mẫu nào và hệ
# thống phải hỏi lại, dù dữ liệu thì có sẵn.
TEMPLATE_THIET_BI = {
    "meta": {
        "noi_gui": {"source": "unit.ten_don_vi"},
        "noi_nhan": {"source": "literal", "value": "Phòng Hành chính nhân sự"},
        "nguoi_ky": {"source": "input", "label": "Tên người ký"},
        "ngay_bao_cao": {"source": "today"},
    },
    "sections": [
        {"id": "thiet_bi", "title": "I. TÌNH TRẠNG TRANG THIẾT BỊ", "type": "table",
         "query": "thiet_bi",
         "columns": ["ten_thiet_bi", "so_luong", "tinh_trang", "cap_nhat_cuoi"],
         "narrative": "Sau bảng, nêu tổng số lượng thiết bị và số chủng loại. "
                      "Không nhận xét về tình trạng nếu bảng chưa có nhãn tình trạng."},
        {"id": "de_xuat", "title": "II. ĐỀ XUẤT, KIẾN NGHỊ", "type": "llm",
         "narrative": "Nêu kiến nghị rà soát dựa trên số lượng và chủng loại đã nêu ở "
                      "mục I. Không suy ra tình trạng, hạn bảo dưỡng hay nhu cầu thay "
                      "thế của bất kỳ thiết bị nào - số liệu không có những thông tin đó."},
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
        ten_bao_cao="Báo cáo tổng hợp nhân sự và trang thiết bị",
        loai_bao_cao="bao_cao_dinh_ky",
        mo_ta=(
            "Dùng khi cấp trên yêu cầu báo cáo đồng thời về nhân sự và tình trạng trang "
            "thiết bị của đơn vị, thường phục vụ xây dựng kế hoạch bổ sung thiết bị hoặc "
            "điều chỉnh biên chế. Gồm ba phần: nhân sự, bảng trang thiết bị, và đề xuất."
        ),
        file_path="data/templates/bao_cao_tai_nguyen.docx",
        truong_du_lieu=json.dumps(TEMPLATE_TAI_NGUYEN, ensure_ascii=False),
    ),
    TemplateBaoCao(
        ma_template="BC_NHANSU",
        ten_bao_cao="Báo cáo nhân sự",
        loai_bao_cao="bao_cao_dinh_ky",
        mo_ta=(
            "Dùng khi chỉ cần báo cáo về nhân sự, biên chế, không đề cập trang thiết bị. "
            "Thường gửi Phòng Hành chính nhân sự theo định kỳ tháng hoặc quý."
        ),
        truong_du_lieu=json.dumps(TEMPLATE_NHAN_SU, ensure_ascii=False),
    ),
    TemplateBaoCao(
        ma_template="BC_THIETBI",
        ten_bao_cao="Báo cáo trang thiết bị",
        loai_bao_cao="bao_cao_dinh_ky",
        mo_ta=(
            "Dùng khi chỉ cần báo cáo về trang thiết bị, tài sản, máy móc của MỘT "
            "đơn vị, không đề cập nhân sự. Gồm bảng thiết bị và phần kiến nghị."
        ),
        truong_du_lieu=json.dumps(TEMPLATE_THIET_BI, ensure_ascii=False),
    ),
    TemplateBaoCao(
        ma_template="BC_TONGHOP",
        ten_bao_cao="Báo cáo tổng hợp nhân sự và trang thiết bị toàn công ty",
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


# Công văn đến mở đầu kịch bản demo. `ma_don_vi`/`ky` để trống là ĐÚNG: đây là
# văn bản ĐẾN, không phải báo cáo của một đơn vị cho một kỳ.
VAN_BAN = VanBan(
    ma_van_ban="105/CV-BGĐ",
    ten_van_ban="Công văn về việc tổng hợp, báo cáo tình trạng trang thiết bị và nhân sự",
    loai_van_ban="cong_van_den",
    noi_gui="Ban Giám đốc",
    noi_nhan="Các phòng ban, đơn vị trực thuộc",
    mo_ta="Yêu cầu rà soát nhân sự, thống kê trang thiết bị và đề xuất bổ sung cho năm 2027. "
          "Hạn báo cáo: 20/9/2026, gửi về Phòng Hành chính nhân sự.",
    ngay_van_ban=date(2026, 9, 5),
    file_path="data/demo/CV-105-BGD.md",
    dang_file="md",
)


async def main(reset: bool) -> int:
    await create_all()

    ma_template = [t.ma_template for t in TEMPLATES]

    async with session_scope() as session:
        if reset:
            # Xoá ĐÚNG những dòng script này tạo, không `drop_all`. Sổ `van_ban`
            # trên máy đang chạy có hàng chục báo cáo thật, và số ký hiệu của báo
            # cáo mới đếm từ sổ đó - xoá cả bảng là tua ngược bộ đếm về 01 rồi
            # ghi đè lên bản đã phát hành.
            await session.execute(
                delete(TemplateBaoCao).where(TemplateBaoCao.ma_template.in_(ma_template))
            )
            await session.execute(
                delete(VanBan).where(VanBan.ma_van_ban == VAN_BAN.ma_van_ban)
            )
            print(f"Đã xoá {len(ma_template)} mẫu báo cáo và 1 văn bản demo.")

        for template in TEMPLATES:
            await session.merge(template)
        await session.merge(VAN_BAN)

    print(f"Đã nạp: {len(TEMPLATES)} mẫu báo cáo ({', '.join(ma_template)}), 1 văn bản demo.")
    print("Số liệu nhân sự/thiết bị lấy từ ERP lúc chạy, script này không đụng tới.")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nạp dữ liệu mẫu cho demo")
    parser.add_argument("--reset", action="store_true",
                        help="Xoá những dòng script này tạo rồi nạp lại (không đụng dòng khác)")
    ns = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(main(ns.reset)))
