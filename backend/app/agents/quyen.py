"""Nghiệp vụ nào cần quyền nào, và câu từ chối khi thiếu quyền.

Tên quyền KHÔNG do dự án này đặt ra - chúng đã có sẵn trong ERP (`AbpPermissions`),
và chính đội quản trị ERP bật/tắt chúng cho từng vai trò. Đặt tên riêng rồi tự
ánh xạ nghĩa là có hai nơi định nghĩa quyền, và chúng sẽ trôi khỏi nhau.

Nhóm `Ai.*` vốn được sinh ra cho đúng module này: `Ai.AiChatbot`,
`Ai.ReportSummary.*`, `Ai.Slide.*`. Ánh xạ bên dưới gần như một-một.
"""

from __future__ import annotations

from app.core.context import Principal, current_principal

# Quyền tối thiểu để chạy một nghiệp vụ.
QUYEN_THEO_NGHIEP_VU: dict[str, str] = {
    "qa": "Ai.AiChatbot",
    "agent": "Ai.AiChatbot",
    "document": "Ai.AiChatbot",
    "draft": "Ai.ReportSummary.Create",
    "report": "Ai.ReportSummary.Create",
    "presentation": "Ai.Slide.Create",
}

# Quyền để đọc từng mảng số liệu. Thiếu thì mảng đó bị loại khỏi báo cáo chứ
# không làm hỏng cả yêu cầu - người dùng vẫn nhận được phần họ có quyền xem.
QUYEN_THEO_MANG: dict[str, str] = {
    "nhan_su": "Hrm.EmployeeProfile.View",
    "thiet_bi": "Asm.Asset.View",
}

# `Hrm.EmployeeProfile.ViewMine` = chỉ xem hồ sơ của CHÍNH MÌNH.
#
# Chưa cài lọc "chỉ dòng của tôi", và đây là lý do: quét toàn CSDL không có lấy
# một vai trò nào có `ViewMine` mà thiếu `View`. Viết một tầng lọc cho trường hợp
# chưa tồn tại là viết thứ không ai chạy thử được.
#
# Hành vi hiện tại vẫn ĐÚNG HƯỚNG AN TOÀN: chốt ở `call_tool` đòi
# `Hrm.EmployeeProfile.View`, nên người chỉ có `ViewMine` bị chặn khỏi công cụ
# nhân sự - chặt hơn ý của ERP, nhưng không lộ dữ liệu.
#
# Khi nào ERP thật sự có vai trò `ViewMine`-only: cầu nối đã có sẵn,
# `AbpUsers.Id` -> `Dms_Employee.UserId` -> `Hrm_EmployeeProfile.EmployeeId`.
QUYEN_CHI_CUA_MINH = "Hrm.EmployeeProfile.ViewMine"

_TEN_VIET: dict[str, str] = {
    "qa": "tra cứu tài liệu",
    "agent": "tra số liệu",
    "document": "soát tài liệu",
    "draft": "soạn văn bản",
    "report": "tổng hợp báo cáo",
    "presentation": "tạo slide",
}


def duoc_chay(intent: str, principal: Principal | None = None) -> bool:
    """Danh tính hiện tại có được chạy nghiệp vụ này không."""
    quyen = QUYEN_THEO_NGHIEP_VU.get(intent)
    if quyen is None:
        return True
    return (principal or current_principal()).can(quyen)


def cau_tu_choi(intent: str, principal: Principal | None = None) -> str:
    """Câu báo cho người dùng khi họ không có quyền.

    Nói rõ THIẾU QUYỀN NÀO và ai cấp được, thay vì một câu "không được phép"
    trống rỗng: người đọc phải biết đi hỏi ai thì mới xong việc. Không nêu tên
    vai trò của người khác, cũng không gợi ý cách đi vòng.
    """
    p = principal or current_principal()
    viec = _TEN_VIET.get(intent, intent)
    quyen = QUYEN_THEO_NGHIEP_VU.get(intent, "")
    vai = ", ".join(p.access.role_names) if p.access and p.access.role_names else "chưa rõ"
    return (
        f"Tài khoản của bạn không có quyền {viec}. "
        f"Quyền cần có: `{quyen}`. Vai trò hiện tại: {vai}. "
        f"Liên hệ quản trị hệ thống ERP để được cấp."
    )


def loc_mang_duoc_xem(noi_dung: list[str], principal: Principal | None = None) -> list[str]:
    """Bỏ khỏi báo cáo những mảng số liệu người này không được xem.

    Trả về danh sách rỗng nghĩa là không còn gì để làm - bên gọi phải coi đó là
    từ chối, chứ không phải "báo cáo trống".
    """
    p = principal or current_principal()
    return [m for m in noi_dung if p.can(QUYEN_THEO_MANG.get(m, ""))
            or QUYEN_THEO_MANG.get(m) is None]


# --------------------------------------------------------------------------- #
# Chốt cho endpoint gọi thẳng workflow
#
# Đường agent (`/api/agent/chat`) đi qua `_call_branch` nên có chốt sẵn. Các
# endpoint chuyên biệt thì KHÔNG - và đã có lúc chúng là một đường vòng thật:
# tài khoản 0 quyền bị chặn ở `/api/agent/chat` vẫn gọi `/api/reports/draft` ra
# được file .docx. Chốt phải nằm ở CẢ HAI đường, nếu không nó chỉ là trang trí.
#
# Kho tri thức không có quyền tương ứng bên ERP (nhóm `Ai.*` chỉ nói tới chatbot,
# báo cáo và slide). Nên ánh xạ ở đây là lựa chọn của dự án này, ghi ra để ai đọc
# cũng biết đó không phải thứ lấy từ ERP:
#   - đọc kho (thống kê, bộ tiêu chí)  -> như dùng chatbot
#   - nạp tài liệu vào kho             -> như tạo ra nội dung trong hệ thống
#   - XOÁ tài liệu khỏi kho            -> chỉ Admin, vì hỏng thì không lấy lại được
# --------------------------------------------------------------------------- #
QUYEN_KHO_DOC = "Ai.AiChatbot"
QUYEN_KHO_GHI = "Ai.ReportSummary.Create"


class ThieuQuyen(Exception):
    """Không đủ quyền chạy việc này. Tầng API đổi thành HTTP 403."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def can_quyen(permission: str, viec: str = ""):
    """Dependency FastAPI: chặn request nếu danh tính hiện tại thiếu quyền.

    Dùng `Depends(can_quyen("Ai.Slide.Create", "tạo slide"))` trên endpoint.
    """
    def _kiem_tra() -> None:
        p = current_principal()
        if p.can(permission):
            return
        vai = ", ".join(p.access.role_names) if p.access and p.access.role_names else "chưa rõ"
        raise ThieuQuyen(
            f"Tài khoản của bạn không có quyền {viec or permission}. "
            f"Quyền cần có: `{permission}`. Vai trò hiện tại: {vai}. "
            f"Liên hệ quản trị hệ thống ERP để được cấp."
        )
    return _kiem_tra


def can_admin(viec: str = ""):
    """Dependency: chỉ Admin (vai trò tĩnh của ABP) mới qua được."""
    def _kiem_tra() -> None:
        p = current_principal()
        # Chưa tra được quyền thì không chặn - xem `Principal.can`.
        if p.access is None or p.access.is_static_admin:
            return
        vai = ", ".join(p.access.role_names) or "chưa rõ"
        raise ThieuQuyen(
            f"Chỉ quản trị viên mới {viec or 'làm được việc này'}. "
            f"Vai trò hiện tại: {vai}."
        )
    return _kiem_tra
