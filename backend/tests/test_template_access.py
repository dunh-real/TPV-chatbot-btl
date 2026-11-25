import pytest

from app.services.template_access import allows_template


@pytest.mark.parametrize("raw,expected", [
    ('{"all":true,"departmentIds":[],"userGroupIds":[]}', True),
    ('{"All":false,"DepartmentIds":[49,50,51],"UserGroupIds":[1]}', True),
    ('{"All":false,"DepartmentIds":[49],"UserGroupIds":[1]}', False),
    ('{"All":false,"UserGroupIds":[2]}', True),
    ('{"All":false}', False),
    ('{"All":"true"}', False),
    ('{"DepartmentIds":"50"}', False),
    ('invalid', False),
    ('null', False),
    ('[]', False),
    (None, True),
])
def test_usage_scope(raw, expected):
    assert allows_template(raw, {50}, {2}) is expected
