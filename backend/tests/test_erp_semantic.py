"""Kiểm tra semantic lọc quyền, phát hiện schema hỏng và cập nhật YAML ngay lượt sau."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from app.services.erp_sql_catalog import AuthorizedCatalog
from app.tools.base import ToolError


@pytest.fixture
def context(tmp_path):
    snapshot = {"schema_version": "schema-test", "tables": {
        "dbo.asm_assets": {"name": "dbo.Asm_Assets", "columns": {
            "id": {"name": "Id"}, "quantity": {"name": "Quantity"},
            "purchaseprice": {"name": "PurchasePrice"}}},
        "dbo.asm_assetassignmentdetail": {"name": "dbo.Asm_AssetAssignmentDetail", "columns": {
            "quantity": {"name": "Quantity"}}}}}
    tables = {"dbo.asm_assets": deepcopy(snapshot["tables"]["dbo.asm_assets"])}
    del tables["dbo.asm_assets"]["columns"]["purchaseprice"]
    policy = SimpleNamespace(tables=tables, rules={"dbo.asm_assets": {
        "scope": "department", "lookup_columns": []}})
    document = {"version": "1", "tables": {"dbo.Asm_Assets": {
        "meaning": "Danh mục tài sản", "columns": {
            "Quantity": "Số lượng danh mục", "PurchasePrice": "Nội dung bị giới hạn"},
        "caveats": [
            {"text": "Giữ số lượng 0", "requires": ["dbo.Asm_Assets.Quantity"]},
            {"text": "Ghi chú giá bí mật", "requires": ["dbo.Asm_Assets.PurchasePrice"]},
            {"text": "Nguồn cấp phát", "requires": ["dbo.Asm_AssetAssignmentDetail.Quantity"]}],
        "references": ["dbo.Asm_AssetAssignmentDetail"]}}}
    path = tmp_path / "semantic.yaml"
    def write(): path.write_text(yaml.safe_dump(document, allow_unicode=True))
    write()
    return snapshot, policy, document, path, write


def test_only_authorized_semantics_reach_model(context):
    snapshot, policy, _, path, _ = context
    catalog = AuthorizedCatalog(snapshot, policy, path)
    info = catalog.describe_tables(["Asm_Assets"])[0]
    assert info["meaning"] == "Danh mục tài sản"
    assert info["columns"]["quantity"]["meaning"] == "Số lượng danh mục"
    assert info["caveats"] == ["Giữ số lượng 0"] and info["references"] == []
    assert info["access_scope"] == "department"
    assert "bí mật" not in str(catalog.tables) and "PurchasePrice" not in str(catalog.tables)
    assert "meaning" not in policy.tables["dbo.asm_assets"]
    assert "meaning" not in snapshot["tables"]["dbo.asm_assets"]


@pytest.mark.parametrize("change", ["column", "reference", "dependency"])
def test_stale_semantics_are_rejected(context, change):
    snapshot, policy, document, path, write = context
    entry = document["tables"]["dbo.Asm_Assets"]
    if change == "column": entry["columns"]["RemovedField"] = "Cột cũ"
    if change == "reference": entry["references"].append("dbo.RemovedTable")
    if change == "dependency": entry["caveats"].append({"text": "Quy tắc cũ", "requires": ["dbo.Asm_Assets.RemovedField"]})
    write()
    with pytest.raises(ToolError, match="Semantic catalog"):
        AuthorizedCatalog(snapshot, policy, path)


def test_yaml_change_is_loaded_next_request(context):
    snapshot, policy, document, path, write = context
    before = AuthorizedCatalog(snapshot, policy, path)
    document["tables"]["dbo.Asm_Assets"]["meaning"] = "Mô tả mới"
    write()
    after = AuthorizedCatalog(snapshot, policy, path)
    assert before.semantic_version != after.semantic_version
    assert after.tables["dbo.asm_assets"]["meaning"] == "Mô tả mới"


def test_bad_or_missing_yaml_fails_closed(context):
    snapshot, policy, _, path, _ = context
    path.write_text("tables: [invalid")
    with pytest.raises(ToolError): AuthorizedCatalog(snapshot, policy, path)
    path.unlink()
    with pytest.raises(ToolError): AuthorizedCatalog(snapshot, policy, path)


def test_unauthorized_table_semantics_are_never_loaded(context):
    snapshot, policy, document, path, write = context
    document["tables"]["dbo.SecretTable"] = {"meaning": "Thông tin không được đọc", "columns": {"Missing": "bí mật"}}
    write()
    catalog = AuthorizedCatalog(snapshot, policy, path)
    assert "Thông tin không được đọc" not in str(catalog.tables)
