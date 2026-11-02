"""Metadata số nguyên từ ERP và bộ lọc bắt buộc, không suy diễn thứ bậc ID."""
from qdrant_client import models
from sqlalchemy import select

from app.core import runtime
from app.core.config import get_settings
from app.core.context import current_principal
from app.services.integration_errors import IntegrationError

FIELDS = ("document_id", "tenant_id", "partition_id", "access_level_id", "security_level_id")


async def from_document(repo, row):
    result = {"document_id": int(row["Id"]), "tenant_id": row["TenantId"], "partition_id": row["PartitionId"]}
    for field, category in (("access_level_id", "AccessLevel"), ("security_level_id", "SecurityLevel")):
        direct = row.get(category + "CategoryId", row.get(category + "Id"))
        if direct is not None:
            result[field] = int(direct)
        elif row.get(category + "Code"):
            table = repo.table("Categories")
            ids = (await repo.conn.execute(select(table.c.Id).where(repo.scope(table),
                table.c.CategoryType == category, table.c.Code == row[category + "Code"]))).scalars().all()
            if len(ids) != 1:
                raise IntegrationError("CONFIG_INVALID", "Không ánh xạ duy nhất được mã phân loại ERP sang ID", 409)
            result[field] = int(ids[0])
        else:
            result[field] = None
    if result["tenant_id"] is None or result["partition_id"] is None:
        raise IntegrationError("CONFIG_INVALID", "Tài liệu ERP thiếu tenant hoặc phân vùng", 409)
    result["tenant_id"], result["partition_id"] = int(result["tenant_id"]), int(result["partition_id"])
    return result


def restrict(query_filter):
    conditions = []
    cfg = get_settings()
    # Được bật sau khi backfill. SQL whitelist vẫn bắt buộc khi còn ở chế độ chuyển tiếp.
    if cfg.metadata_filter_required:
        tenant = current_principal().tenant_id
        if tenant is None:
            raise IntegrationError("TENANT_MISMATCH", "Thiếu tenant cho truy hồi", 403)
        conditions.append(models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant)))
    filters = runtime.metadata_filters.get() or {}
    active = runtime.agent_config.get()
    for key, name in (("partition_id", "partition_ids"), ("access_level_id", "access_level_ids"),
                      ("security_level_id", "security_level_ids")):
        values = filters.get(name)
        if values is not None:
            conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=values)))
    if active and active.allowedPartitionIds is not None:
        conditions.append(models.FieldCondition(key="partition_id", match=models.MatchAny(any=active.allowedPartitionIds)))
    return models.Filter(must=([query_filter] if query_filter is not None else []) + conditions) if conditions else query_filter
