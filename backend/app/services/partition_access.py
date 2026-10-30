"""Phạm vi kho từ ERP; quyền nhóm dựa thành viên, deny luôn thu hẹp quyền đọc."""
from sqlalchemy import and_, or_, select, exists


def document_scope(repo, doc):
    if repo.principal.is_admin:
        return True
    part = repo.table("DataPartitions")
    valid = [repo.scope(part), part.c.IsActive == True]
    if "Status" in part.c:
        valid.append(part.c.Status == "Active")
    common = part.c.PartitionType == "Common"
    if "WorkDepartmentId" in part.c:
        common = and_(common, part.c.WorkDepartmentId.is_(None))
    own = and_(part.c.PartitionType == "Personal", part.c.OwnerUserId == repo.user_id)
    if "AI_PartitionPermissions" not in repo.tables:
        # Test/DB cũ chưa có bảng quyền: không mở thêm kho chung.
        return doc.c.PartitionId.in_(select(part.c.Id).where(*valid, own))
    member, group = repo.table("UserGroupMembers"), repo.table("UserGroups")
    groups = select(group.c.Id).join(member, member.c.UserGroupId == group.c.Id).where(
        repo.scope(group), repo.scope(member), group.c.IsActive == True,
        group.c.Status == "Active", member.c.UserId == repo.user_id, member.c.LeftTime.is_(None))
    employee = repo.tables["Dms_Employee"]
    units = select(employee.c.WorkDepartmentId).where(employee.c.TenantId == repo.principal.tenant_id,
        employee.c.UserId == repo.user_id, employee.c.IsDeleted == False)
    shared = or_(common, and_(part.c.PartitionType == "Common", part.c.WorkDepartmentId.in_(units)),
                 and_(part.c.PartitionType == "Group", part.c.UserGroupId.in_(groups)))
    rule = repo.table("PartitionPermissions")
    subjects = or_(and_(rule.c.SubjectType == "User", rule.c.UserId == repo.user_id),
        and_(rule.c.SubjectType == "Role", rule.c.RoleId.in_(repo.principal.access.role_ids)),
        and_(rule.c.SubjectType == "Group", rule.c.UserGroupId.in_(groups)),
        and_(rule.c.SubjectType == "Unit", rule.c.WorkDepartmentId.in_(units)))
    def classified(column, legacy, category_id, category_type):
        if legacy in doc.c:
            return column == doc.c[legacy]
        if category_id not in doc.c or "AI_Categories" not in repo.tables:
            return False
        category = repo.table("Categories").alias("routing_" + category_type)
        return exists(select(category.c.Id).where(repo.scope(category),
            category.c.Id == doc.c[category_id], category.c.CategoryType == category_type,
            category.c.Code == column).correlate(doc, rule))
    match = [repo.scope(rule), subjects, rule.c.PartitionId == part.c.Id,
        or_(rule.c.DocumentId.is_(None), rule.c.DocumentId == doc.c.Id),
        or_(rule.c.FolderId.is_(None), rule.c.FolderId == doc.c.FolderId),
        or_(rule.c.FieldCode.is_(None), classified(rule.c.FieldCode, "FieldCode", "FieldCategoryId", "Field")),
        or_(rule.c.DocumentTypeCode.is_(None), classified(rule.c.DocumentTypeCode,
            "DocumentTypeCode", "DocumentTypeCategoryId", "DocumentType"))]
    # Không đoán thứ bậc mã bảo mật; grant có trần chỉ nhận đúng mã đã xác nhận.
    grant = exists(select(rule.c.Id).where(*match, rule.c.IsDeny == False,
        rule.c.PermissionLevel.in_(["ReadOnly", "RagSearch", "Contributor", "Admin"]),
        or_(rule.c.MaxSecurityLevelCode.is_(None), classified(rule.c.MaxSecurityLevelCode, "SecurityLevelCode", "SecurityLevelCategoryId", "SecurityLevel"))).correlate(doc, part))
    # Deny ở thư mục có thể áp cả cây con: thu hẹp toàn phân vùng để không bỏ lọt.
    deny_match = [m for i,m in enumerate(match) if i != 4]
    deny = exists(select(rule.c.Id).where(*deny_match, rule.c.IsDeny == True).correlate(doc, part))
    return exists(select(part.c.Id).where(*valid, part.c.Id == doc.c.PartitionId,
        or_(own, shared, grant), ~deny).correlate(doc))
