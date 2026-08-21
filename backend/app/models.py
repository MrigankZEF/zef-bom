"""
SQLAlchemy models — the ZEF BOM data model.

Design rules (from BOM instruction check.md, refined by the HTML brainstorm + feedback):
  * Derive, don't store: parentage, where-used, and rollups are all queries over
    `bom_links` / `decided_costs` — never denormalised onto the item.
  * Cost is two layers: `cost_evidence` (0..n quotes/invoices/estimates) and
    `decided_costs` (one user-committed number per volume tier, used by rollups).
  * History is a single append-only `change_history` log (no item_revisions).
  * Custom fields are data (`field_definitions` + `field_values`) so new fields
    need no migration.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Portable JSON: JSONB on Postgres (indexable), plain JSON on SQLite for local dev.
# Keeps a single Alembic migration valid on both backends.
JSONB_OR_JSON = JSON().with_variant(JSONB(), "postgresql")


class Item(Base):
    """The master list of physical things: parts and assemblies."""

    __tablename__ = "items"

    # Part number is the natural key, e.g. "AEC001A".
    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)

    item_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'part' | 'assembly'
    is_top_level: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    module_code: Mapped[str | None] = mapped_column(String(8))

    material: Mapped[str | None] = mapped_column(Text)  # deprecated: single material (kept for back-compat)
    materials: Mapped[list | None] = mapped_column(JSONB_OR_JSON)  # list of material names
    weight_grams: Mapped[float | None] = mapped_column(Float)
    unit_of_measure: Mapped[str] = mapped_column(String(16), nullable=False, default="pcs")

    supplier: Mapped[str | None] = mapped_column(Text)
    supplier_country: Mapped[str | None] = mapped_column(String(64))
    lead_time_weeks: Mapped[float | None] = mapped_column(Float)

    # Assemblies only: the assembly cost type — a reference_values row (category
    # 'assembly_cost_type') whose meta carries a €/hour rate. Assembly cost = assembly
    # time (assembly_labor table) × that rate, added ON TOP of the rolled-up children.
    cost_type_id: Mapped[int | None] = mapped_column(Integer)

    drawing_url: Mapped[str | None] = mapped_column(Text)
    drive_folder_url: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    external_reference: Mapped[str | None] = mapped_column(Text)

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(255))

    cost_evidence: Mapped[list["CostEvidence"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    decided_costs: Mapped[list["DecidedCost"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class BomLink(Base):
    """Parent → child structure. Parentage and where-used derive from here only."""

    __tablename__ = "bom_links"
    __table_args__ = (
        UniqueConstraint("parent_item_id", "child_item_id", name="uq_bom_links_parent_child"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    child_item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    unit_of_measure: Mapped[str] = mapped_column(String(16), nullable=False, default="pcs")
    comment: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChangeHistory(Base):
    """Append-only, field-level audit log. Powers per-item history and 'BOM as of date X'."""

    __tablename__ = "change_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    changed_by: Mapped[str | None] = mapped_column(String(255))

    # entity_type: item | bom_link | cost_evidence | decided_cost | field_value | upload_batch
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    field_changed: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)

    change_type: Mapped[str] = mapped_column(String(16), nullable=False)  # create | update | remove
    change_reason: Mapped[str | None] = mapped_column(Text)


class CostEvidence(Base):
    """Optional supporting evidence behind a cost: quotes, invoices, estimates."""

    __tablename__ = "cost_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    # quote | invoice | estimate_math | estimate_web | estimate_ai
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    supplier_country: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="EUR")
    unit_cost: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    volume_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[str | None] = mapped_column(String(8))  # high | medium | low
    cost_min: Mapped[float | None] = mapped_column(Numeric(14, 4))
    cost_max: Mapped[float | None] = mapped_column(Numeric(14, 4))
    note: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(255))

    item: Mapped["Item"] = relationship(back_populates="cost_evidence")


class DecidedCost(Base):
    """The human-committed unit cost per volume tier. Rollups sum these, never auto-pick."""

    __tablename__ = "decided_costs"
    __table_args__ = (
        UniqueConstraint("item_id", "volume_tier", name="uq_decided_cost_item_volume"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    volume_tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 | 100 | 10000 | ...
    unit_cost_eur: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)  # most-likely
    cost_min: Mapped[float | None] = mapped_column(Numeric(14, 4))  # optional 3-point estimate
    cost_max: Mapped[float | None] = mapped_column(Numeric(14, 4))
    confidence: Mapped[str | None] = mapped_column(String(8))
    # Sourcing, and the single source of truth for it: buy (off the shelf) | made-to-order
    # (our specs) | make (in house). Per tier because it genuinely differs by volume —
    # e.g. make@1 as a prototype, buy@10k once a supplier will tool for it.
    make_or_buy: Mapped[str | None] = mapped_column(String(16))
    source_type: Mapped[str | None] = mapped_column(String(24))
    basis_note: Mapped[str | None] = mapped_column(Text)
    based_on_evidence_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cost_evidence.id")
    )

    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[str | None] = mapped_column(String(255))

    item: Mapped["Item"] = relationship(back_populates="decided_costs")


class FieldDefinition(Base):
    """User-extensible custom field. Adding one is a row here — no migration."""

    __tablename__ = "field_definitions"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # enum|number|boolean|url|text
    applies_to: Mapped[str] = mapped_column(String(16), nullable=False, default="both")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    options: Mapped[dict | None] = mapped_column(JSONB_OR_JSON)
    unit: Mapped[str | None] = mapped_column(String(16))
    group: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldValue(Base):
    """Value of a custom field for one item (EAV)."""

    __tablename__ = "field_values"
    __table_args__ = (
        UniqueConstraint("item_id", "field_key", name="uq_field_value_item_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    field_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("field_definitions.key"), nullable=False
    )
    value: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UploadBatch(Base):
    """One Miro OPML import. Carries the diff payload for review / audit / replay."""

    __tablename__ = "upload_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. 'ub-2026-05-22'
    source_filename: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_top_level_bom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # pending_review | approved | rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending_review")
    summary_json: Mapped[dict | None] = mapped_column(JSONB_OR_JSON)


class User(Base):
    """A person who signs in with Google. Role gates admin actions."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="editor")  # admin|editor|viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssemblyLabor(Base):
    """Minutes to assemble an item from its direct children, as a 3-point estimate
    (min/likely/max) per volume tier. Cost = time × the item's cost-type rate."""

    __tablename__ = "assembly_labor"
    __table_args__ = (
        UniqueConstraint("item_id", "volume_tier", name="uq_assembly_labor_item_volume"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("items.item_id"), nullable=False, index=True
    )
    volume_tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 | 100 | 10000
    time_min: Mapped[float | None] = mapped_column(Float)
    time_likely: Mapped[float] = mapped_column(Float, nullable=False)  # minutes, most-likely
    time_max: Mapped[float | None] = mapped_column(Float)
    # "This assembly's cost already covers the work on everything beneath it" — an outsourced
    # or bought-in unit. Per tier, because sourcing differs by volume (build @1, buy @10k).
    # Affects coverage reporting only; the rollup arithmetic is untouched.
    covers_subassemblies: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(255))


class ReferenceValue(Base):
    """Admin-managed dropdown values: suppliers, materials, countries, modules.

    One table for all reference lists, keyed by category. The UI's '+ add' writes
    here; dropdowns read from here. Soft-deletable via `archived`.
    """

    __tablename__ = "reference_values"
    __table_args__ = (
        UniqueConstraint("category", "value", name="uq_reference_category_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)  # supplier|material|country|module
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(Text)  # optional friendly label (e.g. module name)
    meta: Mapped[dict | None] = mapped_column(JSONB_OR_JSON)  # e.g. {"region": "EU"} for a country
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
