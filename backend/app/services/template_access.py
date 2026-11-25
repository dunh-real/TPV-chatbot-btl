"""Quyền sử dụng mẫu độc lập với quyền đọc dữ liệu và người tạo mẫu."""
import json


def allows_template(raw, departments, groups):
    # Giữ tương thích mẫu cũ chưa khai báo phạm vi.
    if not raw:
        return True
    try:
        scope = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(scope, dict):
        return False
    scope = {key.lower(): value for key, value in scope.items()}
    if scope.get("all") is True:
        return True

    def matches(key, memberships):
        ids = scope.get(key, [])
        if not isinstance(ids, list):
            return False
        return any(type(value) is int and value in memberships for value in ids)

    return matches("departmentids", departments) or matches("usergroupids", groups)
