"""Đọc danh tính và quyền của một tài khoản từ ERP.

Bốn bảng, tất cả chỉ ĐỌC: `AbpUsers` (ai), `AbpUserRoles` (mang vai trò nào),
`AbpRoles` (vai trò đó tên gì, có phải Admin tĩnh không), `AbpPermissions`
(vai trò đó được cấp những quyền nào).

VÌ SAO ADMIN TĨNH PHẢI XỬ LÝ RIÊNG

Vai trò `Admin` của ABP có `IsStatic = 1`, và ABP cấp quyền cho nó NGẦM trong mã
nguồn chứ không ghi dòng nào vào `AbpPermissions`. Đọc bảng quyền theo đúng nghĩa
đen thì admin hoá ra gần như không có quyền gì - số liệu thật trên CSDL này:

    tenant 63:0 quyền · 64:0 · 65:0 · 66:0 · 78:0 · 94:7

Bảy dòng của tenant 94 là vài quyền quản trị người dùng được gán thêm, không phải
toàn bộ. Nên `is_static_admin` được tách thành một cờ riêng, và `has()` trả về
True cho mọi quyền khi cờ đó bật. Bỏ nhánh này đi thì tài khoản admin bị chính hệ
thống khoá ra ngoài - đúng cái bẫy mà lần đọc đầu tiên suýt rơi vào.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccountAccess:
    """Một tài khoản ERP, kèm mọi thứ cần để quyết định nó được làm gì."""

    user_id: int
    tenant_id: int | None
    user_name: str
    display_name: str
    email: str
    role_ids: tuple[int, ...] = ()
    role_names: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    is_static_admin: bool = False

    def has(self, permission: str) -> bool:
        """Có quyền này không. Admin tĩnh thì luôn có - xem ghi chú đầu file."""
        return self.is_static_admin or permission in self.permissions

    def has_any(self, *permissions: str) -> bool:
        return any(self.has(p) for p in permissions)

    def describe(self) -> str:
        vai = ", ".join(self.role_names) or "không vai trò"
        so = "toàn quyền (Admin tĩnh)" if self.is_static_admin else f"{len(self.permissions)} quyền"
        return f"{self.user_name} (#{self.user_id}, tenant {self.tenant_id}) - {vai} - {so}"


# Chỉ lấy cột định danh. `Password`, `SecurityStamp`, `SignInToken`,
# `PasswordResetCode`, `GoogleAuthenticatorKey` là bí mật đăng nhập - hệ thống này
# không xác thực ai cả, nên đọc chúng là lấy thứ mình không có việc gì để dùng.
_CHON_USER = """
    SELECT u.Id, u.TenantId, u.UserName, u.Name, u.Surname, u.EmailAddress
    FROM AbpUsers u
    WHERE u.IsDeleted = 0 AND u.IsActive = 1
"""


def _ten_hien_thi(name: str | None, surname: str | None, user_name: str) -> str:
    day_du = f"{(name or '').strip()} {(surname or '').strip()}".strip()
    return day_du or user_name


class ErpAuthRepository:
    """Tra danh tính và quyền. Mọi truy vấn đều là SELECT."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_user(
        self, *, user_id: int | None = None, identifier: str | None = None
    ) -> AccountAccess | None:
        """Tìm theo Id, hoặc theo username/email (không phân biệt hoa thường).

        `identifier` nhận cả ba dạng người dùng hay gõ: username (`bqp1`), email
        (`anh.tuan@company.vn`), hoặc chuỗi đã chuẩn hoá của ABP. Tìm trượt thì
        trả `None` chứ không ném lỗi - gọi ở tầng middleware, và một danh tính
        không tra được không phải là sự cố hệ thống.
        """
        if user_id is not None:
            sql, tham_so = _CHON_USER + " AND u.Id = :uid", {"uid": user_id}
        elif identifier:
            sql = _CHON_USER + """
                AND (UPPER(u.UserName) = UPPER(:ident)
                     OR UPPER(u.EmailAddress) = UPPER(:ident)
                     OR u.NormalizedUserName = UPPER(:ident)
                     OR u.NormalizedEmailAddress = UPPER(:ident))"""
            tham_so = {"ident": identifier.strip()}
        else:
            return None

        row = (await self.session.execute(text(sql), tham_so)).fetchone()
        if row is None:
            return None

        uid, tenant_id, user_name, name, surname, email = row
        roles = await self._roles_of(uid)
        role_ids = tuple(r[0] for r in roles)
        perms = await self._permissions_of(role_ids) if role_ids else frozenset()

        return AccountAccess(
            user_id=uid,
            tenant_id=tenant_id,
            user_name=user_name,
            display_name=_ten_hien_thi(name, surname, user_name),
            email=email or "",
            role_ids=role_ids,
            role_names=tuple(r[1] for r in roles),
            permissions=perms,
            is_static_admin=any(r[2] for r in roles),
        )

    async def _roles_of(self, user_id: int) -> list[tuple[int, str, bool]]:
        """(id, tên hiển thị, có phải Admin tĩnh) của từng vai trò.

        `IsStatic` một mình chưa đủ: vai trò `User` cũng tĩnh mà chẳng có quyền
        gì. Phải kèm `Name = 'Admin'` - đó mới là vai trò ABP cấp quyền ngầm.
        """
        rows = (await self.session.execute(text("""
            SELECT r.Id, r.DisplayName, r.Name, r.IsStatic
            FROM AbpUserRoles ur
            JOIN AbpRoles r ON r.Id = ur.RoleId
            WHERE ur.UserId = :uid AND r.IsDeleted = 0
            ORDER BY r.Id"""), {"uid": user_id})).fetchall()
        return [
            (x[0], (x[1] or x[2] or "").strip(), bool(x[3]) and (x[2] or "").lower() == "admin")
            for x in rows
        ]

    async def _permissions_of(self, role_ids: tuple[int, ...]) -> frozenset[str]:
        """Hợp của các quyền ĐƯỢC CẤP trên mọi vai trò người đó mang.

        `IsGranted = 0` là một dòng từ chối tường minh. Người mang hai vai trò,
        một cấp một từ chối, thì ABP cho qua - nên ở đây cũng lấy hợp của phần
        được cấp, không trừ đi phần bị từ chối.
        """
        if not role_ids:
            return frozenset()
        cho = ", ".join(f":r{i}" for i in range(len(role_ids)))
        tham_so = {f"r{i}": rid for i, rid in enumerate(role_ids)}
        rows = (await self.session.execute(text(
            f"SELECT DISTINCT Name FROM AbpPermissions "
            f"WHERE IsGranted = 1 AND RoleId IN ({cho})"), tham_so)).fetchall()
        return frozenset(x[0] for x in rows)

    async def list_accounts(self, tenant_id: int | None, limit: int = 50) -> list[dict]:
        """Danh sách tài khoản để giao diện demo chọn người đăng nhập giả.

        Kèm luôn số quyền và cờ admin, để người demo nhìn danh sách là biết chọn
        ai thì thấy được gì - không phải bấm thử từng tài khoản.
        """
        dieu_kien = "AND u.TenantId = :tid" if tenant_id is not None else ""
        rows = (await self.session.execute(text(f"""
            SELECT TOP (:lim) u.Id, u.UserName, u.Name, u.Surname, u.EmailAddress,
                   (SELECT COUNT(*) FROM AbpUserRoles ur WHERE ur.UserId = u.Id)
            FROM AbpUsers u
            WHERE u.IsDeleted = 0 AND u.IsActive = 1 {dieu_kien}
            ORDER BY u.Id"""), {"tid": tenant_id, "lim": limit})).fetchall()

        ket_qua = []
        for uid, user_name, name, surname, email, so_vai_tro in rows:
            if not so_vai_tro:
                continue  # không vai trò thì không dùng được để demo phân quyền
            access = await self.find_user(user_id=uid)
            if access is None:
                continue
            ket_qua.append({
                "user_id": access.user_id,
                "user_name": access.user_name,
                "display_name": access.display_name,
                "email": access.email,
                "roles": list(access.role_names),
                "is_admin": access.is_static_admin,
                "permission_count": len(access.permissions),
            })
        return ket_qua
