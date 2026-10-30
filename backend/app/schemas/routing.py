"""Schema API cho workflow phân loại và phân luồng văn bản đến."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoutingDepartment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="Id")
    code: str = Field(alias="Code", min_length=1)
    display_name: str = Field(alias="DisplayName", min_length=1)
    description: str = Field(default="", alias="Description")


class RoutingCategory(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    field_code: str = Field(alias="FieldCode", min_length=1)
    field_name: str = Field(default="", alias="FieldName")
    description: str = Field(default="", alias="Description")


class RoutingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_url: str = Field(default="", alias="FileUrl")
    extracted_text: str = Field(default="", alias="ExtractedText")
    document_id: str = Field(default="", alias="document_id", max_length=100)
    departments: list[RoutingDepartment] = Field(default_factory=list, alias="Departments")
    categories: list[RoutingCategory] = Field(default_factory=list, alias="Categories")
    urgency_levels: list[str] = Field(
        default_factory=lambda: ["Thuong", "Khan", "Hoa toc"], alias="UrgencyLevels"
    )
    force_refresh: bool = Field(default=False, alias="force_refresh")


class RoutingEvidence(BaseModel):
    quote: str
    page: int | None = None
    block_id: str = ""


class RoutingAiResult(BaseModel):
    """Output giới hạn của model trước khi backend map và lưu."""

    document_type_code: str | None = None
    field_code: str | None = None
    urgency_level: str | None = None
    primary_unit_code: str | None = None
    coordinating_unit_codes: list[str] = Field(default_factory=list)
    reason: str = ""
    evidence: list[RoutingEvidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ai_primary_unit_id: int | None = Field(default=None, alias="AiPrimaryUnitId")
    ai_coordinating_units_json: str | None = Field(default=None, alias="AiCoordinatingUnitsJson")
    urgency_level: str | None = Field(default=None, alias="UrgencyLevel")
    review_note: str = Field(default="", alias="ReviewNote", max_length=2000)


class RoutingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    routing_id: str = Field(alias="RoutingId")
    document_id: str = Field(alias="document_id")
    file_id: str = Field(default="", alias="FileId")
    status: str = Field(alias="Status")
    document_type_code: str | None = Field(default=None, alias="DocumentTypeCode")
    field_code: str | None = Field(default=None, alias="FieldCode")
    urgency_level: str | None = Field(default=None, alias="UrgencyLevel")
    ai_primary_unit_id: int | None = Field(default=None, alias="AiPrimaryUnitId")
    ai_primary_unit_code: str | None = Field(default=None, alias="AiPrimaryUnitCode")
    ai_coordinating_units_json: str = Field(default="[]", alias="AiCoordinatingUnitsJson")
    ai_coordinating_unit_codes: list[str] = Field(default_factory=list, alias="AiCoordinatingUnitCodes")
    ai_reason: str = Field(default="", alias="AiReason")
    ai_evidence_json: list[dict[str, Any]] = Field(default_factory=list, alias="AiEvidenceJson")
    ai_confidence: float | None = Field(default=None, alias="AiConfidence")
    warnings: list[str] = Field(default_factory=list, alias="Warnings")
    department_catalog: list[dict[str, Any]] = Field(default_factory=list, alias="DepartmentCatalog")
    category_catalog: list[dict[str, Any]] = Field(default_factory=list, alias="CategoryCatalog")
    created_at: str | None = Field(default=None, alias="CreatedAt")
    reviewed_by: str | None = Field(default=None, alias="ReviewedBy")
    reviewed_at: str | None = Field(default=None, alias="ReviewedAt")
    review_note: str = Field(default="", alias="ReviewNote")