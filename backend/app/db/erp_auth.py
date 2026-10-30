"""Đọc quyền ERP theo role và quyền trực tiếp; không cấp quyền theo ID tài khoản.

User grant/deny được xét trước quyền role. Các role lấy hợp quyền được cấp;
role từ chối không trừ grant của role khác. Ngoại lệ Admin giữ tương thích qua
ERP_STATIC_ADMIN_FULL_ACCESS, không phải quy tắc suy ra được chỉ từ schema DB.
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
    denied_permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        """Deny trực tiếp chặn cả grant role; ngoại lệ Admin không bỏ qua deny user."""
        return permission not in self.denied_permissions and (
            self.is_static_admin or permission in self.permissions)

    def has_any(self, *permissions: str) -> bool:
        return any(self.has(p) for p in permissions)

    def describe(self) -> str:
        vai = ", ".join(self.role_names) or "không vai trò"
        so = "Admin (áp dụng deny trực tiếp)" if self.is_static_admin else f"{len(self.permissions)} quyền"
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

        row = (await self.session.execute(text(sql), tham_so)).fetchmany(2)
        if len(row) != 1:
            return None

        uid, tenant_id, user_name, name, surname, email = row[0]
        roles = await self._roles_of(uid, tenant_id)
        role_ids = tuple(r[0] for r in roles)
        perms = await self._permissions_of(role_ids, tenant_id) if role_ids else frozenset()
        direct = (await self.session.execute(text(
            "SELECT Name, IsGranted FROM AbpPermissions WHERE UserId=:uid AND RoleId IS NULL "
            "AND (TenantId=:tenant OR (TenantId IS NULL AND :tenant IS NULL))"),
            {"uid": uid, "tenant": tenant_id})).fetchall()
        granted = {name for name, value in direct if value}
        denied = {name for name, value in direct if not value} - granted
        from app.core.config import get_settings

        return AccountAccess(
            user_id=uid,
            tenant_id=tenant_id,
            user_name=user_name,
            display_name=_ten_hien_thi(name, surname, user_name),
            email=email or "",
            role_ids=role_ids,
            role_names=tuple(r[1] for r in roles),
            permissions=frozenset((set(perms) | granted) - denied),
            denied_permissions=frozenset(denied),
            is_static_admin=any(r[2] for r in roles) and get_settings().erp_static_admin_full_access,
        )

    async def _roles_of(self, user_id: int, tenant_id: int | None) -> list[tuple[int, str, bool]]:
        """(id, tên hiển thị, có phải Admin tĩnh) của từng vai trò.

        `IsStatic` một mình chưa đủ: vai trò `User` cũng tĩnh mà chẳng có quyền
        gì. Phải kèm `Name = 'Admin'` - đó mới là vai trò ABP cấp quyền ngầm.
        """
        rows = (await self.session.execute(text("""
            SELECT r.Id, r.DisplayName, r.Name, r.IsStatic
            FROM AbpUserRoles ur
            JOIN AbpRoles r ON r.Id = ur.RoleId
            WHERE ur.UserId = :uid AND r.IsDeleted = 0
              AND (ur.TenantId = :tenant OR (ur.TenantId IS NULL AND :tenant IS NULL))
              AND (r.TenantId = :tenant OR (r.TenantId IS NULL AND :tenant IS NULL))
            ORDER BY r.Id"""), {"uid": user_id, "tenant": tenant_id})).fetchall()
        return [
            (x[0], (x[1] or x[2] or "").strip(), bool(x[3]) and (x[2] or "").lower() == "admin")
            for x in rows
        ]

    async def _permissions_of(self, role_ids: tuple[int, ...], tenant_id: int | None) -> frozenset[str]:
        """Hợp của các quyền ĐƯỢC CẤP trên mọi vai trò người đó mang.

        `IsGranted = 0` là một dòng từ chối tường minh. Người mang hai vai trò,
        một cấp một từ chối, thì ABP cho qua - nên ở đây cũng lấy hợp của phần
        được cấp, không trừ đi phần bị từ chối.
        """
        if not role_ids:
            return frozenset()
        cho = ", ".join(f":r{i}" for i in range(len(role_ids)))
        tham_so = {f"r{i}": rid for i, rid in enumerate(role_ids)}
        tham_so["tenant"] = tenant_id
        rows = (await self.session.execute(text(
            f"SELECT RoleId, Name, IsGranted FROM AbpPermissions "
            f"WHERE RoleId IN ({cho}) AND UserId IS NULL "
            "AND (TenantId = :tenant OR (TenantId IS NULL AND :tenant IS NULL))"), tham_so)).fetchall()
        grants, denials = {}, {}
        for role, name, value in rows:
            (grants if value else denials).setdefault(role, set()).add(name)
        # Cấu hình trùng grant/deny trong cùng role không có thứ tự đáng tin cậy: không mở quyền đó.
        return frozenset(name for role, names in grants.items()
                         for name in names - denials.get(role, set()))

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
