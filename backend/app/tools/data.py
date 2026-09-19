"""Công cụ truy vấn số liệu nghiệp vụ cho báo cáo tổng hợp.

Đây là tầng DUY NHẤT chạm vào số liệu. LLM không sinh SQL, không nhận quyền truy
vấn, và không phải tính bất cứ phép nào: mọi con số sẽ xuất hiện trong câu văn -
kể cả tỷ lệ phần trăm và mức tăng giảm - đều được tính sẵn ở đây.

Nguyên tắc kiểm tra: nếu một con số trong báo cáo không có mặt trong kết quả của
các tool này thì nó là số bịa.

Nguồn số liệu là CSDL ERP và quyền được cấp là CHỈ ĐỌC trên một số bảng. Vài chỉ
tiêu của bản thiết kế cũ vì thế không còn nguồn (chấm công, kiểm kê chốt theo kỳ);
chúng bị BỎ HẲN khỏi kết quả chứ không được thay bằng số suy đoán. `scope` của
mỗi kết quả liệt kê rõ những chỉ tiêu vắng mặt và lý do.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.erp_models import EmployeeProfile
from app.db.erp_repository import (
    AS_OF_NOTE,
    ErpChucVuRepository,
    ErpDonViRepository,
    ErpNhanSuRepository,
    ErpTrangBiRepository,
    period_end,
    period_start,
)
from app.db.models import VanBan
from app.tools.base import ToolError, ToolSpec

logger = logging.getLogger(__name__)

KY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

__all__ = [
    "AggregateResult", "ConsistencyIssue", "Metric", "ToolError", "ToolSpec", "TOOLS",
    "EQUIPMENT_DIMENSIONS", "PERSONNEL_DIMENSIONS",
    "call_tool", "describe_tools", "get_equipment_statistics", "get_personnel_statistics",
    "get_reporting_status", "previous_ky", "validate_group_by", "validate_ky",
]

# --------------------------------------------------------------------------- #
# Chiều gộp: DANH SÁCH ĐÓNG, không phải tên cột do model đặt
#
# Mở rộng biến thể truy vấn bằng cách này thay vì để model sinh SQL. Lý do không
# phải là sợ lệnh phá hoại - phiên ERP đã chặn mọi câu ghi - mà là:
#
#   1. Ngữ nghĩa "as-of" quá dễ viết sai mà vẫn ra một con số trông hợp lý: bỏ
#      sót một vế của `IsDeleted`/`DeletionTime` là số của kỳ cũ đổi luôn.
#   2. `IN (...)` không khớp `NULL`, nên câu SQL "đúng theo trực giác" âm thầm
#      bỏ 49/194 trang bị chưa gán phòng ban.
#   3. Van chắn số kiểm "con số này có trong kết quả tool không". Nếu chính câu
#      truy vấn do model đặt ra thì mọi con số đều có trong kết quả - kể cả khi
#      nó trả lời một câu hỏi khác.
#
# Ở đây model chỉ chọn một KHOÁ; code quyết định cột, phép nối và phép gộp.
PERSONNEL_DIMENSIONS: dict[str, str] = {"phong_ban": "Đơn vị", "chuc_vu": "Chức vụ"}
EQUIPMENT_DIMENSIONS: dict[str, str] = {"phong_ban": "Đơn vị", "chung_loai": "Chủng loại"}


def validate_group_by(value: Any, allowed: dict[str, str]) -> str:
    """Chiều gộp phải nằm trong danh sách. Sai thì chặn, không đoán ý."""
    key = str(value or "phong_ban").strip().lower()
    if key not in allowed:
        raise ToolError(f"Không gộp theo {value!r} được. "
                        f"Chỉ dùng được: {', '.join(allowed)}")
    return key


@dataclass(slots=True)
class Metric:
    """Một chỉ tiêu kèm sẵn mọi con số sẽ được nhắc tới trong văn bản."""

    value: int | float
    prev: int | float | None = None
    delta: int | float | None = None
    delta_pct: float | None = None
    share_pct: float | None = None

    @classmethod
    def build(cls, value, prev=None, total=None) -> "Metric":
        delta = None if prev is None else round(value - prev, 2)
        delta_pct = None
        if prev:
            delta_pct = round((value - prev) / prev * 100, 1)
        share_pct = round(value / total * 100, 1) if total else None
        return cls(value=value, prev=prev, delta=delta, delta_pct=delta_pct,
                   share_pct=share_pct)


@dataclass(slots=True)
class ConsistencyIssue:
    ma_don_vi: str
    message: str


@dataclass(slots=True)
class AggregateResult:
    period: str
    compare_to: str | None
    scope: dict[str, Any]
    metrics: dict[str, Metric] = field(default_factory=dict)
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    consistency: list[ConsistencyIssue] = field(default_factory=list)
    computed_at: str = ""
    # Chiều đã gộp, và các cột của bảng chi tiết kèm nhãn tiếng Việt.
    #
    # Cột do chính tool khai chứ không để mỗi nơi dựng bảng tự viết cứng: trước
    # đây `report.py` và `presentation.py` mỗi bên giữ một danh sách cột riêng,
    # nên thêm một chiều gộp là phải sửa đúng ba chỗ và chỉ cần quên một chỗ là
    # bảng in ra nhãn "Đơn vị" trên một cột đang chứa tên chức vụ.
    dimension: str = "phong_ban"
    breakdown_columns: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "compare_to": self.compare_to,
            "scope": self.scope,
            "metrics": {k: asdict(v) for k, v in self.metrics.items()},
            "breakdown": self.breakdown,
            "breakdown_columns": self.breakdown_columns,
            "dimension": self.dimension,
            "consistency": [asdict(c) for c in self.consistency],
            "computed_at": self.computed_at or datetime.now().isoformat(timespec="seconds"),
        }

    @property
    def is_consistent(self) -> bool:
        return not self.consistency


# --------------------------------------------------------------------------- #
# Tiện ích kỳ
# --------------------------------------------------------------------------- #
def validate_ky(ky: str) -> str:
    if not isinstance(ky, str) or not KY_RE.match(ky):
        raise ToolError(f'Kỳ phải có dạng "YYYY-MM", nhận được {ky!r}')
    return ky


def previous_ky(ky: str) -> str:
    year, month = int(ky[:4]), int(ky[5:])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


async def _valid_units(session: AsyncSession) -> dict[str, str]:
    return await ErpDonViRepository(session).name_map()


async def _resolve_units(session: AsyncSession, ma_don_vi: list[str] | str | None) -> list[str]:
    units = await _valid_units(session)
    if not ma_don_vi:
        return sorted(units)
    requested = [ma_don_vi] if isinstance(ma_don_vi, str) else list(ma_don_vi)
    unknown = [code for code in requested if code not in units]
    if unknown:
        raise ToolError(f"Đơn vị không tồn tại: {', '.join(unknown)}")
    return requested


async def _unit_ids(session: AsyncSession, codes: list[str]) -> dict[str, int]:
    """Mã đơn vị -> khoá chính ERP. Trang bị và nhân sự đều nối bằng khoá này."""
    id_map = await ErpDonViRepository(session).id_map()
    return {code: id_map[code] for code in codes if code in id_map}


# --------------------------------------------------------------------------- #
# Tool 1: thống kê quân số
# --------------------------------------------------------------------------- #
async def get_personnel_statistics(
    session: AsyncSession,
    ky: str,
    ma_don_vi: list[str] | str | None = None,
    compare_to: str | None = None,
    group_by: str = "phong_ban",
) -> AggregateResult:
    """Quân số tại thời điểm chốt kỳ, kèm biến động trong kỳ.

    Nguồn là `Hrm_EmployeeProfile`: quân số suy từ ngày vào làm / ngày nghỉ việc.
    Không có chỉ tiêu có mặt / vắng / đi học / nghỉ phép vì dữ liệu chấm công nằm
    ở nhóm bảng `Att_*` ngoài phạm vi được phép đọc - thà thiếu chỉ tiêu còn hơn
    đưa ra một con số không đối chiếu được.

    `group_by` đổi CHIỀU GỘP của bảng chi tiết, không đổi phạm vi lọc: "quân số
    Phòng Kế toán theo chức vụ" vẫn chỉ đếm người của Phòng Kế toán. Chỉ tiêu
    tổng vì thế không đổi theo chiều gộp - đó là phép kiểm rẻ nhất cho việc này.
    """
    ky = validate_ky(ky)
    group_by = validate_group_by(group_by, PERSONNEL_DIMENSIONS)
    compare_to = validate_ky(compare_to) if compare_to else previous_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)
    ids = await _unit_ids(session, units)
    dept_ids = list(ids.values())

    # Hỏi toàn cơ quan thì hồ sơ chưa gán phòng ban vẫn là quân số của cơ quan;
    # hỏi vài đơn vị cụ thể thì không gán bừa vào đơn vị nào.
    toan_co_quan = not ma_don_vi

    # Cột gộp do CODE chọn từ khoá model đưa vào; model không đặt tên cột.
    cot_nhom = EmployeeProfile.work_position_id if group_by == "chuc_vu" else None

    nhan_su = ErpNhanSuRepository(session)
    current = await nhan_su.headcount_by_dept(period_end(ky), dept_ids,
                                              gom_chua_gan=toan_co_quan,
                                              cot_nhom=cot_nhom)
    previous = await nhan_su.headcount_by_dept(period_end(compare_to), dept_ids,
                                               gom_chua_gan=toan_co_quan,
                                               cot_nhom=cot_nhom)
    bien_dong = await nhan_su.movement(period_start(ky), period_end(ky), dept_ids,
                                       gom_chua_gan=toan_co_quan,
                                       cot_nhom=cot_nhom)

    quan_so = sum(current.values())
    prev_quan_so = sum(previous.values())
    tuyen_moi = sum(bien_dong["tuyen_moi"].values())
    nghi_viec = sum(bien_dong["nghi_viec"].values())

    metrics = {
        "total_personnel": Metric.build(quan_so, prev_quan_so or None),
        "new_hires": Metric.build(tuyen_moi, total=quan_so),
        "resignations": Metric.build(nghi_viec, total=quan_so),
    }

    # Ràng buộc kiểm được bằng chính dữ liệu đọc ra: chênh lệch quân số giữa hai
    # kỳ phải bằng số tuyển mới trừ số nghỉ việc. Lệch nghĩa là hồ sơ thiếu ngày
    # vào làm/nghỉ việc, hoặc có người bị chuyển phòng ban giữa kỳ.
    issues: list[ConsistencyIssue] = []
    if prev_quan_so and quan_so - prev_quan_so != tuyen_moi - nghi_viec:
        issues.append(ConsistencyIssue(
            "*", f"Quân số tăng {quan_so - prev_quan_so} nhưng tuyển mới {tuyen_moi} - "
                 f"nghỉ việc {nghi_viec} = {tuyen_moi - nghi_viec}. Chênh lệch thường do "
                 f"hồ sơ thiếu ngày vào làm/nghỉ việc hoặc có điều chuyển phòng ban."))

    def dong(ma: str, ten: str, khoa: Any) -> dict[str, Any]:
        """Một dòng của bảng chi tiết - cùng bộ chỉ tiêu cho mọi chiều gộp."""
        return {"ma_nhom": ma, "ten_nhom": ten,
                "quan_so": current.get(khoa, 0),
                "quan_so_ky_truoc": previous.get(khoa, 0),
                "tuyen_moi": bien_dong["tuyen_moi"].get(khoa, 0),
                "nghi_viec": bien_dong["nghi_viec"].get(khoa, 0)}

    if group_by == "chuc_vu":
        chuc_vu = ErpChucVuRepository(session)
        ten_cv, ma_cv = await chuc_vu.name_by_id(), await chuc_vu.code_by_id()
        # Chỉ liệt kê chức vụ CÓ NGƯỜI trong một trong hai kỳ: danh mục chức vụ
        # của ERP dài và phần lớn bỏ trống, bảng đầy dòng 0 thì không ai đọc.
        khoa_co_nguoi = {k for k in (*current, *previous) if k is not None}
        breakdown = sorted(
            (dong(ma_cv.get(k, str(k)), ten_cv.get(k, f"Chức vụ {k}"), k)
             for k in khoa_co_nguoi),
            key=lambda r: r["ten_nhom"])
        chua_gan_nhan = "(chưa gán chức vụ)"
    else:
        breakdown = [dong(code, names.get(code, code), ids[code]) for code in sorted(ids)]
        chua_gan_nhan = "(chưa gán phòng ban)"

    # Dòng chưa gán vẫn phải xuất hiện, nếu không tổng sẽ không khớp với tổng các
    # dòng và người đọc không biết số chênh đi đâu.
    if current.get(None, 0) or previous.get(None, 0):
        breakdown.append(dong("", chua_gan_nhan, None))

    # Bảng quân số giữ nguyên `ma_don_vi`/`ten_don_vi` khi gộp theo phòng ban:
    # bước đối chiếu với báo cáo đơn vị (`reconcile_node`) tra theo hai khoá đó.
    if group_by == "phong_ban":
        for row in breakdown:
            row["ma_don_vi"], row["ten_don_vi"] = row["ma_nhom"], row["ten_nhom"]

    scope: dict[str, Any] = {
        "units_requested": len(units),
        "nguon": "Hrm_EmployeeProfile", "ghi_chu": AS_OF_NOTE,
        "nhom_theo": PERSONNEL_DIMENSIONS[group_by],
        "khong_co_chi_tieu": ["có mặt", "vắng", "đi học", "nghỉ phép"],
    }
    # Số liệu đã gộp theo chiều khác thì không còn biết đơn vị nào có/không có
    # dữ liệu. BỎ HẲN hai chỉ tiêu đó thay vì để lại giá trị tính từ khoá sai:
    # `current` lúc này khoá theo chức vụ, tra bằng id phòng ban sẽ ra 0 hết và
    # báo cáo in ra "cả 9 đơn vị chưa cung cấp dữ liệu".
    if group_by == "phong_ban":
        scope["units_with_data"] = sum(1 for code in ids if current.get(ids[code]))
        scope["units_missing"] = sorted(code for code in units
                                        if not current.get(ids.get(code, -1)))
        # ĐẾM cũng là một con số sẽ xuất hiện trong câu văn: "8 đơn vị có số liệu,
        # 1 đơn vị chưa cung cấp". Chỉ có danh sách thì con số 1 ấy không truy về
        # đâu được, van chắn số kết luận là bịa và chặn cả báo cáo - đúng một câu
        # đúng sự thật làm hỏng cả file. Nguyên tắc ở đầu tệp này: mọi con số sẽ
        # được nhắc tới đều phải tính sẵn ở đây.
        scope["units_missing_count"] = len(scope["units_missing"])

    return AggregateResult(
        period=ky, compare_to=compare_to if prev_quan_so else None,
        scope=scope,
        metrics=metrics, breakdown=breakdown, consistency=issues,
        dimension=group_by,
        breakdown_columns=[
            {"key": "ten_nhom", "label": PERSONNEL_DIMENSIONS[group_by]},
            {"key": "quan_so", "label": "Quân số"},
            {"key": "quan_so_ky_truoc", "label": "Kỳ trước"},
            {"key": "tuyen_moi", "label": "Tuyển mới"},
            {"key": "nghi_viec", "label": "Nghỉ việc"},
        ],
    )


# --------------------------------------------------------------------------- #
# Tool 2: thống kê trang thiết bị
# --------------------------------------------------------------------------- #
async def get_equipment_statistics(
    session: AsyncSession,
    ky: str,
    ma_don_vi: list[str] | str | None = None,
    compare_to: str | None = None,
    group_by: str = "phong_ban",
) -> AggregateResult:
    """Trang thiết bị tại thời điểm chốt kỳ.

    Hai chiều gộp cho ra hai kiểu bảng khác nhau, cố ý:

      - `phong_ban`  bảng LIỆT KÊ, mỗi dòng một đầu trang bị của một đơn vị.
        Đây là bảng chi tiết mà người nghe đối chiếu con số tổng về từng đơn vị.
      - `chung_loai` bảng ĐÃ CỘNG, mỗi dòng một chủng loại.

    Chỉ tiêu tổng giống nhau ở cả hai, vì chúng cộng trên cùng một tập dòng.
    """
    ky = validate_ky(ky)
    group_by = validate_group_by(group_by, EQUIPMENT_DIMENSIONS)
    compare_to = validate_ky(compare_to) if compare_to else previous_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)
    ids = await _unit_ids(session, units)
    dept_ids = list(ids.values())
    by_id = {dept_id: code for code, dept_id in ids.items()}

    toan_co_quan = not ma_don_vi

    repo = ErpTrangBiRepository(session)
    current = await repo.list_as_of(period_end(ky), dept_ids, gom_chua_gan=toan_co_quan)
    previous = await repo.list_as_of(period_end(compare_to), dept_ids,
                                     gom_chua_gan=toan_co_quan)

    settings = get_settings()
    labels, good_codes = settings.asset_status_labels, settings.asset_status_good

    def summarize(rows) -> tuple[int, int, int]:
        tong = sum(asset.so_luong for _, asset, _ in rows)
        tot = sum(asset.so_luong for _, asset, _ in rows if asset.status in good_codes)
        return tong, tot, tong - tot

    tong, tot, can_xu_ly = summarize(current)
    prev_tong, prev_tot, prev_can = summarize(previous)

    metrics = {
        "total_equipment": Metric.build(tong, prev_tong or None),
        # Đếm TÊN trang bị khác nhau, không phải chủng loại ERP - xem
        # `METRIC_LABELS` ở `app.agents.nodes.report`.
        "equipment_types": Metric.build(len({asset.name for _, asset, _ in current})),
    }
    # Chưa khai báo ERP_ASSET_STATUS_GOOD thì không có cách nào biết mã trạng thái
    # nào là "tốt". Bỏ hẳn hai chỉ tiêu này thay vì mặc định coi tất cả là tốt -
    # một con số sai ở đây sẽ đi thẳng vào báo cáo trình ký.
    if good_codes:
        metrics["good"] = Metric.build(tot, prev_tot or None, total=tong)
        metrics["needs_attention"] = Metric.build(can_xu_ly, prev_can or None, total=tong)

    def sort_key(row) -> tuple[str, str]:
        """Dòng chưa gán phòng ban xuống cuối bảng, không lẫn lên đầu.

        Mã đơn vị rỗng sắp xếp trước mọi mã thật, nên phải đẩy tay xuống - giống
        chỗ đặt dòng "(chưa gán phòng ban)" của bảng quân số.
        """
        ma = by_id.get(row[0], "")
        return (ma or "\uffff", row[1].name)

    if group_by == "chung_loai":
        # Cộng theo chủng loại. Trang bị không có chủng loại vẫn phải vào bảng,
        # nếu không tổng các dòng sẽ nhỏ hơn chỉ tiêu tổng in ở slide trước đó.
        gom: dict[str, dict[str, Any]] = {}
        for _, asset, category in current:
            ten = category or "(chưa phân loại)"
            dong_gom = gom.setdefault(ten, {"ma_nhom": ten, "ten_nhom": ten,
                                            "so_luong": 0, "so_dau_muc": 0})
            dong_gom["so_luong"] += asset.so_luong
            dong_gom["so_dau_muc"] += 1
        breakdown = sorted(gom.values(),
                           key=lambda r: (r["ten_nhom"] == "(chưa phân loại)",
                                          -r["so_luong"]))
        breakdown_columns = [
            {"key": "ten_nhom", "label": "Chủng loại"},
            {"key": "so_luong", "label": "Số lượng"},
            # Số BẢN GHI trang bị trong chủng loại đó. Không cộng lại thành
            # chỉ tiêu "Số loại trang bị" - hai cách đếm khác nhau.
            {"key": "so_dau_muc", "label": "Số đầu mục"},
        ]
    else:
        breakdown = [
            {"ma_nhom": by_id.get(dept_id, ""),
             "ten_nhom": names.get(by_id.get(dept_id, ""), "(chưa gán phòng ban)"),
             "ma_don_vi": by_id.get(dept_id, ""),
             "ten_don_vi": names.get(by_id.get(dept_id, ""), "(chưa gán phòng ban)"),
             "ten_trang_bi": asset.name, "so_luong": asset.so_luong,
             "tinh_trang": labels.get(asset.status, f"Trạng thái {asset.status}"),
             "chung_loai": category or "",
             # Xem ghi chú ở `ErpTaiNguyenRepository.get_tai_nguyen`: đây là mốc
             # sửa bản ghi ERP, không phải ngày bảo dưỡng.
             "cap_nhat_cuoi": asset.last_modification_time.date().isoformat()
             if asset.last_modification_time else None}
            for dept_id, asset, category in sorted(current, key=sort_key)
        ]
        breakdown_columns = [
            {"key": "ten_nhom", "label": "Đơn vị"},
            {"key": "ten_trang_bi", "label": "Trang bị"},
            {"key": "so_luong", "label": "Số lượng"},
            {"key": "tinh_trang", "label": "Tình trạng"},
        ]

    chua_gan = sum(asset.so_luong for dept_id, asset, _ in current if dept_id is None)

    scope: dict[str, Any] = {
        "units_requested": len(units),
        "nguon": "Asm_Assets", "ghi_chu": AS_OF_NOTE,
        "nhom_theo": EQUIPMENT_DIMENSIONS[group_by],
    }
    # Đếm theo đơn vị chỉ có nghĩa khi bảng đang gộp theo đơn vị - xem ghi chú
    # cùng chỗ này ở tool quân số.
    if group_by == "phong_ban":
        scope["units_with_data"] = len(
            {dept_id for dept_id, _, _ in current if dept_id is not None})
        scope["units_missing"] = sorted(
            set(units) - {by_id.get(d, "") for d, _, _ in current})
        # Xem ghi chú cùng chỗ này ở tool quân số.
        scope["units_missing_count"] = len(scope["units_missing"])
    if not good_codes:
        scope["khong_co_chi_tieu"] = [
            "tình trạng tốt", "cần xử lý",
            "(chưa khai báo ERP_ASSET_STATUS_GOOD nên không diễn giải được mã trạng thái)",
        ]

    # Trang bị chưa gán phòng ban được cộng vào tổng nhưng không quy được về đơn vị
    # nào. Người đọc phải biết điều đó, nếu không sẽ thắc mắc vì sao tổng lớn hơn
    # tổng các dòng - hoặc tệ hơn, không thắc mắc gì cả.
    issues: list[ConsistencyIssue] = []
    if chua_gan:
        scope["chua_gan_don_vi"] = chua_gan
        issues.append(ConsistencyIssue(
            "", f"{chua_gan}/{tong} đơn vị trang bị chưa gán phòng ban trong ERP "
                f"(Asm_Assets.WorkDepartmentId trống). Đã tính vào tổng toàn cơ quan "
                f"nhưng không chia được về đơn vị nào."))

    return AggregateResult(
        period=ky, compare_to=compare_to if prev_tong else None,
        metrics=metrics, breakdown=breakdown, scope=scope, consistency=issues,
        dimension=group_by, breakdown_columns=breakdown_columns,
    )


# --------------------------------------------------------------------------- #
# Tool 3: tình hình nộp báo cáo
# --------------------------------------------------------------------------- #
def _don_vi_cua(van_ban: VanBan, units: list[str], names: dict[str, str]) -> list[str]:
    """Văn bản này là của đơn vị nào, trong phạm vi đang hỏi."""
    if van_ban.ma_don_vi:
        return [van_ban.ma_don_vi] if van_ban.ma_don_vi in units else []
    noi_gui = van_ban.noi_gui or ""
    return [code for code in units if names.get(code) and names[code] in noi_gui]


async def get_reporting_status(
    session: AsyncSession, ky: str, ma_don_vi: list[str] | str | None = None
) -> dict[str, Any]:
    """Đơn vị nào đã gửi báo cáo kỳ này, đơn vị nào chưa.

    Thông tin có giá trị nhất của một báo cáo tổng hợp thường không phải con số
    tổng, mà là "ai chưa nộp".
    """
    ky = validate_ky(ky)
    units = await _resolve_units(session, ma_don_vi)
    names = await _valid_units(session)

    year, month = int(ky[:4]), int(ky[5:])
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    # Sổ văn bản do chính hệ thống này ghi nên nằm ở CSDL app, không phải ERP.
    from app.db.session import session_scope

    async with session_scope() as app_session:
        result = await app_session.execute(
            select(VanBan).where(VanBan.loai_van_ban == "bao_cao_di")
        )
        van_ban_list = list(result.scalars())

    submitted: dict[str, dict[str, Any]] = {}
    for van_ban in van_ban_list:
        # Bản ghi mới có sẵn mã đơn vị và kỳ -> khớp thẳng, không phải đoán.
        #
        # Bản ghi cũ thì dò theo tên đơn vị trong `noi_gui` và lấy `ngay_van_ban`
        # làm mốc kỳ. Cách cũ giữ lại để sổ văn bản có sẵn không rỗng đi sau khi
        # đổi lược đồ, nhưng nó sai một cách có hệ thống: báo cáo kỳ tháng 8 ký
        # ngày 19/9 bị tính là ngoài kỳ, nên mục "tình hình gửi báo cáo" luôn ra
        # 0 đơn vị đã gửi.
        for code in _don_vi_cua(van_ban, units, names):
            if van_ban.ky:
                in_period = van_ban.ky == ky
            else:
                in_period = van_ban.ngay_van_ban is None or start <= van_ban.ngay_van_ban < end
            if code not in submitted or in_period:
                submitted[code] = {
                    "ma_van_ban": van_ban.ma_van_ban,
                    "ngay_van_ban": van_ban.ngay_van_ban.isoformat()
                    if van_ban.ngay_van_ban else None,
                    "file_path": van_ban.file_path,
                    "ky": van_ban.ky,
                    "trong_ky": in_period,
                }

    reported = sorted(code for code, info in submitted.items() if info["trong_ky"])
    missing = sorted(set(units) - set(reported))
    return {
        "period": ky,
        "units_total": len(units),
        "units_reported": len(reported),
        "reported": [{"ma_don_vi": c, "ten_don_vi": names.get(c, c), **submitted[c]}
                     for c in reported],
        "missing": [{"ma_don_vi": c, "ten_don_vi": names.get(c, c)} for c in missing],
    }


# --------------------------------------------------------------------------- #
# Registry - LLM chỉ được chọn trong danh sách này
# --------------------------------------------------------------------------- #
# Bí danh theo khoảng thời gian: người dùng nói "từ tháng 6 đến tháng 9" chứ không
# nói "kỳ 2026-09, so sánh với 2026-06". Số liệu kiểm kê chốt theo tháng nên một
# khoảng quy về đúng hai mốc: kỳ báo cáo (cuối khoảng) và kỳ đối chiếu (đầu khoảng).
PERIOD_ALIASES = {"end_date": "ky", "start_date": "compare_to", "unit": "ma_don_vi",
                  "nhom_theo": "group_by", "theo": "group_by"}

TOOLS: dict[str, ToolSpec] = {
    "get_personnel_statistics": ToolSpec(
        name="get_personnel_statistics",
        description="Thống kê quân số theo kỳ (tổng quân số tại thời điểm chốt kỳ, "
                    "tuyển mới, nghỉ việc trong kỳ), kèm so sánh với kỳ trước. "
                    "Bảng chi tiết gộp theo đơn vị hoặc theo chức vụ. "
                    "Không có số liệu có mặt/vắng/đi học/nghỉ phép",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "mã đơn vị hoặc danh sách mã; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)",
                    "compare_to": "kỳ để so sánh; bỏ trống = kỳ liền trước "
                                  "(bí danh: start_date)",
                    "group_by": 'chiều gộp bảng chi tiết: "phong_ban" (mặc định) hoặc '
                                '"chuc_vu". Không đổi phạm vi lọc, chỉ đổi cách chia '
                                'dòng (bí danh: nhom_theo, theo)'},
        func=get_personnel_statistics,
        needs_session=True,
        aliases=PERIOD_ALIASES,
    ),
    "get_equipment_statistics": ToolSpec(
        name="get_equipment_statistics",
        description="Thống kê trang thiết bị theo kỳ (tổng số lượng, số chủng loại; "
                    "thêm tình trạng tốt/cần xử lý nếu đã khai báo mã trạng thái ERP). "
                    "Bảng chi tiết liệt kê theo đơn vị, hoặc cộng theo chủng loại",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "mã đơn vị hoặc danh sách mã; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)",
                    "compare_to": "kỳ để so sánh; bỏ trống = kỳ liền trước "
                                  "(bí danh: start_date)",
                    "group_by": 'chiều gộp bảng chi tiết: "phong_ban" (mặc định, liệt kê '
                                'từng đầu trang bị) hoặc "chung_loai" (cộng theo chủng '
                                'loại) (bí danh: nhom_theo, theo)'},
        func=get_equipment_statistics,
        needs_session=True,
        aliases=PERIOD_ALIASES,
    ),
    "get_reporting_status": ToolSpec(
        name="get_reporting_status",
        description="Danh sách đơn vị đã gửi và chưa gửi báo cáo trong kỳ",
        parameters={"ky": "YYYY-MM, bắt buộc (bí danh: end_date)",
                    "ma_don_vi": "giới hạn trong các đơn vị này; bỏ trống = toàn cơ quan "
                                 "(bí danh: unit)"},
        func=get_reporting_status,
        needs_session=True,
        aliases={"end_date": "ky", "unit": "ma_don_vi"},
    ),
}


def describe_tools() -> str:
    """Mô tả tool để đưa vào prompt chọn tool."""
    return "\n".join(spec.describe() for spec in TOOLS.values())


def resolve_aliases(spec: ToolSpec, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Đổi bí danh về tên tham số thật; bí danh trùng với tên thật thì tên thật thắng."""
    resolved = {k: v for k, v in kwargs.items() if k not in spec.aliases}
    for alias, real in spec.aliases.items():
        if alias in kwargs and real not in kwargs:
            resolved[real] = kwargs[alias]
    return resolved


async def call_tool(session: AsyncSession, name: str, **kwargs) -> Any:
    """Gọi tool theo tên; tên lạ hoặc tham số sai đều bị chặn tại đây."""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Không có công cụ tên {name!r}. "
                        f"Chỉ dùng được: {', '.join(TOOLS)}")
    kwargs = resolve_aliases(spec, kwargs)
    allowed = set(spec.parameters)
    unknown = set(kwargs) - allowed
    if unknown:
        raise ToolError(f"{name}: tham số không hợp lệ {sorted(unknown)}")
    return await spec.func(session, **kwargs)
