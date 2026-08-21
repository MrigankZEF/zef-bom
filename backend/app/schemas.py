"""Pydantic schemas for request/response bodies. Grows per milestone."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: str
    item_name: str
    item_type: str
    is_top_level: bool
    module_code: str | None = None
    material: str | None = None
    materials: list[str] | None = None
    weight_grams: float | None = None
    unit_of_measure: str
    supplier: str | None = None
    supplier_country: str | None = None
    lead_time_weeks: float | None = None
    cost_type_id: int | None = None
    drawing_url: str | None = None
    drive_folder_url: str | None = None
    comment: str | None = None
    external_reference: str | None = None
    archived: bool = False
    updated_at: datetime | None = None


class ItemPatch(BaseModel):
    """All optional — only present fields are updated (M4)."""

    item_name: str | None = None
    item_type: str | None = None
    # is_top_level is deliberately NOT patchable — it re-codes the whole subtree.
    # Use POST /items/{id}/top-level, which validates and runs the naming engine.
    material: str | None = None
    materials: list[str] | None = None
    weight_grams: float | None = None
    unit_of_measure: str | None = None
    supplier: str | None = None
    supplier_country: str | None = None
    lead_time_weeks: float | None = None
    cost_type_id: int | None = None
    drawing_url: str | None = None
    comment: str | None = None
    change_reason: str | None = None


class CostEvidenceIn(BaseModel):
    source_type: str  # quote | invoice | estimate_math | estimate_web | estimate_ai
    supplier_name: str | None = None
    supplier_country: str | None = None
    currency: str = "EUR"
    unit_cost: float
    volume_tier: int
    effective_date: date | None = None
    confidence: str | None = None
    cost_min: float | None = None
    cost_max: float | None = None
    note: str | None = None
    attachment_url: str | None = None
    change_reason: str | None = None


class DecidedCostIn(BaseModel):
    volume_tier: int
    unit_cost_eur: float  # most-likely (required)
    cost_min: float | None = None
    cost_max: float | None = None
    confidence: str | None = None
    make_or_buy: str | None = None
    source_type: str | None = None
    basis_note: str | None = None
    based_on_evidence_id: int | None = None
    change_reason: str | None = None


class AssemblyLaborIn(BaseModel):
    volume_tier: int
    time_likely: float  # minutes, most-likely (required)
    time_min: float | None = None
    time_max: float | None = None
    # one quoted cost already covers the work on everything beneath this assembly
    covers_subassemblies: bool = False


class AssemblyLaborOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: str
    volume_tier: int
    time_min: float | None = None
    time_likely: float
    time_max: float | None = None
    covers_subassemblies: bool = False


class AddChildIn(BaseModel):
    child_id: str
    quantity: float = 1


class UpdateLinkIn(BaseModel):
    # A zero-quantity link is a data bug, not a valid state — remove the child instead.
    quantity: float = Field(gt=0)


class CreateBomIn(BaseModel):
    item_name: str
    module: str  # the system this BOM belongs to (AEC, DAC, MDAC, …) — not a universal


class MoveLinkIn(BaseModel):
    from_parent: str          # the assembly the part is currently under
    to_parent: str            # the assembly to move it to (must be in the same BOM)
    quantity: float | None = None  # keep the existing quantity unless overridden


class NewItemIn(BaseModel):
    item_name: str
    item_type: str = "part"  # part | assembly
    module: str = "UN"  # UN (universal, stays UN) or a system code like AEC / DAC / MDAC
    allow_duplicate: bool = False  # true → add even though a part with this name already exists


class FieldValueIn(BaseModel):
    field_key: str
    value: str | None = None


class ReferenceIn(BaseModel):
    category: str  # supplier | material | country | module
    value: str
    label: str | None = None
    meta: dict | None = None


class UserIn(BaseModel):
    email: str
    name: str | None = None
    role: str = "viewer"  # admin | editor | viewer


class UserRoleIn(BaseModel):
    role: str


class ChangeHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    changed_at: datetime
    changed_by: str | None = None
    entity_type: str
    entity_id: str
    field_changed: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    change_type: str
    change_reason: str | None = None


class CostEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: str
    source_type: str
    supplier_name: str | None = None
    supplier_country: str | None = None
    currency: str
    unit_cost: float
    volume_tier: int
    effective_date: date | None = None
    confidence: str | None = None
    cost_min: float | None = None
    cost_max: float | None = None
    note: str | None = None
    attachment_url: str | None = None


class DecidedCostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: str
    volume_tier: int
    unit_cost_eur: float
    cost_min: float | None = None
    cost_max: float | None = None
    confidence: str | None = None
    make_or_buy: str | None = None
    source_type: str | None = None
    basis_note: str | None = None
    based_on_evidence_id: int | None = None


class RollupOut(BaseModel):
    root: str
    volume_tier: int
    cost: float
    covered: int
    total: int
    coverage: float
    missing: list[str]
