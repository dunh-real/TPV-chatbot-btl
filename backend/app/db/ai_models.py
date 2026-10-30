"""Ánh xạ bảng AI có sẵn bằng reflection; không tạo bảng hoặc sửa schema."""
from sqlalchemy import MetaData, Table

TABLES = (
    "AI_Categories", "AI_PartitionPermissions", "AI_UserGroupMembers", "AI_UserGroups", "Dms_Employee",
    "AI_KnowledgeDocuments", "AI_DataPartitions", "AI_ReportTemplate",
    "AI_TemplateVersions", "AI_ChatConversations", "AI_ChatMessages",
    "AI_Tasks", "AI_DocumentReviews", "AI_DocumentDrafts", "AI_ReportSummaries",
    "AI_Slides", "AI_DocumentRoutings", "AI_OutputVersions", "AI_SourceReferences", "AI_MindMaps", "AI_ExecutionLogs",
)


def reflect(connection):
    metadata = MetaData()
    return {name: Table(name, metadata, autoload_with=connection, resolve_fks=False)
            for name in TABLES}
