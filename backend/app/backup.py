"""Full-database backup: dump every table to one .xlsx workbook, and (when Drive is
configured) push snapshots to a Backups folder on Google Drive.

Three triggers share this code:
  * manual    — the "Backup now" download + "Back up to Drive now" button (admin)
  * pre-wipe  — taken automatically right before the destructive catalog import, so a
                wipe is always reversible
  * scheduled — a monthly snapshot with retention, run by the in-app scheduler
"""
from __future__ import annotations

import datetime as _dt
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import drive
from .models import (
    AssemblyLabor, BomLink, ChangeHistory, CostEvidence, DecidedCost, FieldDefinition,
    FieldValue, Item, ReferenceValue, UploadBatch, User,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# One sheet per table. Order is for human readability, not restore.
BACKUP_SHEETS = [
    ("Items", Item),
    ("BomLinks", BomLink),
    ("DecidedCosts", DecidedCost),
    ("CostEvidence", CostEvidence),
    ("AssemblyLabor", AssemblyLabor),
    ("FieldDefinitions", FieldDefinition),
    ("FieldValues", FieldValue),
    ("Reference", ReferenceValue),
    ("UploadBatches", UploadBatch),
    ("ChangeHistory", ChangeHistory),
    ("Users", User),
]

# Scheduled snapshots carry this marker so retention only ever trims THEM — never the
# manual or pre-wipe safety snapshots.
AUTO_MARKER = "-auto"
SCHEDULED_RETENTION = 12  # keep the last N monthly snapshots


def _backup_cell(v):
    """Make any column value safe for an .xlsx cell: JSON for dict/list (JSON columns),
    ISO strings for datetimes (avoids openpyxl's no-timezone error)."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


def build_backup_workbook(db: Session) -> bytes:
    """Dump every table to a single multi-sheet .xlsx and return the bytes."""
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for sheet, model in BACKUP_SHEETS:
            cols = [c.name for c in model.__table__.columns]
            recs = [
                {c: _backup_cell(getattr(obj, c)) for c in cols}
                for obj in db.execute(select(model)).scalars().all()
            ]
            pd.DataFrame(recs, columns=cols).to_excel(xw, sheet_name=sheet[:31], index=False)
    return buf.getvalue()


def backup_filename(reason: str, *, monthly: bool = False) -> str:
    """e.g. zef-bom-backup-2026-06-15-1432-prewipe.xlsx, or ...-2026-06-auto.xlsx (monthly)."""
    now = _dt.datetime.now()
    if monthly:
        return f"zef-bom-backup-{now:%Y-%m}{AUTO_MARKER}.xlsx"
    return f"zef-bom-backup-{now:%Y-%m-%d-%H%M}-{reason}.xlsx"


def run_drive_backup(db: Session, *, reason: str, monthly: bool = False) -> dict:
    """Build a snapshot and upload it to the Drive Backups folder. For monthly snapshots,
    trim old auto-backups to the retention limit. Returns a status dict (never raises —
    a backup failure must not block the action it is protecting)."""
    if not drive.enabled():
        return {"saved": False, "reason": "drive_not_configured"}
    try:
        data = build_backup_workbook(db)
        name = backup_filename(reason, monthly=monthly)
        up = drive.upload_backup(name, data, XLSX_MIME)
        result = {"saved": True, "name": up["name"], "url": up["url"]}
        if monthly:
            result["trimmed"] = _apply_retention()
        return result
    except Exception as exc:  # noqa: BLE001 — surface, but never block the caller
        return {"saved": False, "reason": "error", "error": str(exc)}


def _apply_retention() -> int:
    """Trash scheduled (auto) snapshots beyond the retention limit. Returns count trashed."""
    autos = [f for f in drive.list_backups() if AUTO_MARKER in (f.get("name") or "")]
    # list_backups is newest-first; keep the first N, trash the rest.
    trashed = 0
    for f in autos[SCHEDULED_RETENTION:]:
        try:
            drive.trash_file(f["id"])
            trashed += 1
        except Exception:  # noqa: BLE001
            pass
    return trashed


def monthly_backup_if_due(db: Session) -> dict:
    """Create this month's scheduled snapshot if one doesn't already exist on Drive.
    Idempotent — safe to call repeatedly (e.g. daily) and across restarts."""
    if not drive.enabled():
        return {"saved": False, "reason": "drive_not_configured"}
    try:
        tag = f"{_dt.datetime.now():%Y-%m}{AUTO_MARKER}"
        existing = [f for f in drive.list_backups() if tag in (f.get("name") or "")]
        if existing:
            return {"saved": False, "reason": "already_done_this_month"}
    except Exception as exc:  # noqa: BLE001
        return {"saved": False, "reason": "error", "error": str(exc)}
    return run_drive_backup(db, reason="auto", monthly=True)
