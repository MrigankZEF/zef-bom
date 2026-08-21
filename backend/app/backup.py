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

from sqlalchemy import (
    Boolean, Date, DateTime, Float, Integer, JSON, Numeric, delete, select, text,
)
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
    """Make any column value safe for an .xlsx cell — and safe to *round-trip through Excel*:
    JSON text for dict/list (JSON columns), ISO strings for datetimes (avoids openpyxl's
    no-timezone error), and TRUE/FALSE *text* for booleans. Booleans are written as text on
    purpose: a real boolean cell gets rewritten by Excel as an `=TRUE()`/`=FALSE()` formula when
    the file is edited and re-saved, which then reads back as blank — so a user who edits a
    backup (e.g. to bulk-fill weights) and restores it would wipe every is_top_level/archived
    flag. Text survives untouched. (Restore reads both forms — see `_coerce`.)"""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
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


# ── restore (rebuild the DB from a backup workbook) ──────────────────────────
# Sheet → model. NOTE: Users is intentionally NOT restored — a data restore must never
# change who can sign in (that would risk locking out the current admin). Everything else
# is replaced wholesale. Order satisfies foreign keys on insert (parents before children):
# items first; cost_evidence before decided_costs (decided_costs.based_on_evidence_id →
# cost_evidence.id); field_definitions before field_values.
RESTORE_ORDER = [
    ("Items", Item),
    ("FieldDefinitions", FieldDefinition),
    ("Reference", ReferenceValue),
    ("UploadBatches", UploadBatch),
    ("CostEvidence", CostEvidence),
    ("DecidedCosts", DecidedCost),
    ("AssemblyLabor", AssemblyLabor),
    ("FieldValues", FieldValue),
    ("BomLinks", BomLink),
    ("ChangeHistory", ChangeHistory),
]
RESTORE_MODELS = dict(RESTORE_ORDER)


def _is_na(v) -> bool:
    import pandas as pd

    if v is None:
        return True
    if isinstance(v, (list, dict)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _coerce(value, column):
    """Turn a spreadsheet cell back into the right Python type for its column. Lenient:
    a value that can't be coerced becomes None rather than failing the whole restore."""
    import pandas as pd

    if _is_na(value):
        return None
    t = column.type
    try:
        if isinstance(t, JSON):
            if isinstance(value, (dict, list)):
                return value
            s = str(value).strip()
            return json.loads(s) if s else None
        if isinstance(t, DateTime):
            if isinstance(value, str):
                return _dt.datetime.fromisoformat(value)
            if isinstance(value, pd.Timestamp):
                return value.to_pydatetime()
            return value
        if isinstance(t, Date):
            if isinstance(value, str):
                return _dt.date.fromisoformat(value[:10])
            if isinstance(value, pd.Timestamp):
                return value.date()
            if isinstance(value, _dt.datetime):
                return value.date()
            return value
        if isinstance(t, Boolean):
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            # Text form — including Excel's `=TRUE()`/`=FALSE()` (booleans edited & re-saved in
            # Excel come back as those formulas) and plain "TRUE"/"FALSE"/"1"/"0"/"yes".
            s = str(value).strip().lower().replace("=", "").replace("()", "")
            return s in ("true", "1", "1.0", "yes", "t", "y")
        if isinstance(t, Integer):
            return int(float(value))
        if isinstance(t, (Numeric, Float)):
            return float(value)
        return str(value)
    except Exception:  # noqa: BLE001 — bad cell → null, never abort the restore
        return None


def read_backup_workbook(raw: bytes) -> dict:
    """Open every sheet of a backup .xlsx into {sheet_name: DataFrame}.

    Read with openpyxl (data_only=False) rather than pandas.read_excel: pandas reads formula
    cells with data_only=True and hands back blanks, but a boolean edited & re-saved in Excel
    becomes an `=TRUE()`/`=FALSE()` *formula*. Preserving the formula text lets `_coerce` recover
    the real value instead of nulling every is_top_level/archived flag on restore."""
    import openpyxl
    import pandas as pd

    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=False, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not read the backup .xlsx: {exc}") from exc
    sheets: dict = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            sheets[ws.title] = pd.DataFrame()
            continue
        header = ["" if h is None else str(h) for h in rows[0]]
        body = [r for r in rows[1:] if any(c is not None for c in r)]  # drop fully-blank rows
        sheets[ws.title] = pd.DataFrame(body, columns=header)
    wb.close()
    return sheets


def plan_restore(sheets: dict) -> dict:
    """Validate a backup workbook and report what a restore would do — without touching
    the DB. A valid backup must have an 'Items' sheet with an 'item_id' column."""
    known = [name for name, _ in RESTORE_ORDER if name in sheets]
    unknown = [s for s in sheets if s not in RESTORE_MODELS and s != "Users"]
    counts, column_issues = {}, {}
    for name, model in RESTORE_ORDER:
        df = sheets.get(name)
        counts[name] = 0 if df is None else int(len(df))
        if df is None:
            continue
        model_cols = {c.name for c in model.__table__.columns}
        sheet_cols = set(map(str, df.columns))
        missing = sorted(model_cols - sheet_cols)
        extra = sorted(sheet_cols - model_cols)
        if missing or extra:
            column_issues[name] = {"missing": missing, "extra": extra}

    items_df = sheets.get("Items")
    has_items = items_df is not None and len(items_df) > 0 and "item_id" in map(str, items_df.columns)
    return {
        "ok": bool(has_items),
        "reason": None if has_items else "The backup has no usable 'Items' sheet — this doesn't look like a ZEF BOM backup.",
        "counts": counts,
        "total_items": counts.get("Items", 0),
        "missing_sheets": [name for name, _ in RESTORE_ORDER if name not in sheets],
        "unknown_sheets": unknown,
        "column_issues": column_issues,
        "users_note": "Users / sign-in access are preserved (not restored).",
    }


def restore_from_workbook(db: Session, sheets: dict) -> dict:
    """Replace all BOM data with the backup's contents, atomically. Only columns present in
    BOTH the sheet and the current schema are written (resilient to schema drift); ids are
    preserved so internal references stay intact. Commits on success, rolls back on error."""
    plan = plan_restore(sheets)
    if not plan["ok"]:
        raise ValueError(plan["reason"])

    try:
        # wipe in reverse FK order (children before parents); Users left untouched
        for name, model in reversed(RESTORE_ORDER):
            db.execute(delete(model))
        db.flush()

        counts = {}
        for name, model in RESTORE_ORDER:
            df = sheets.get(name)
            counts[name] = 0
            if df is None or len(df) == 0:
                continue
            cols = {c.name: c for c in model.__table__.columns}
            usable = [c for c in map(str, df.columns) if c in cols]
            rows = [
                {c: _coerce(rec[c], cols[c]) for c in usable}
                for rec in df.to_dict(orient="records")
            ]
            if rows:
                db.execute(model.__table__.insert(), rows)
                db.flush()
            counts[name] = len(rows)

        # Postgres: realign autoincrement sequences so future inserts don't collide with
        # the preserved ids (SQLite needs no fixup).
        if db.bind.dialect.name == "postgresql":
            for name, model in RESTORE_ORDER:
                pk = list(model.__table__.primary_key.columns)[0]
                if isinstance(pk.type, Integer):
                    tbl, col = model.__tablename__, pk.name
                    try:
                        db.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{tbl}', '{col}'), "
                            f"(SELECT COALESCE(MAX({col}), 1) FROM {tbl}))"
                        ))
                    except Exception:  # noqa: BLE001 — table may have no sequence; ignore
                        pass

        _migrate_legacy_sourcing(db, sheets)

        db.commit()
        return {"restored": True, "counts": counts}
    except Exception:
        db.rollback()
        raise


def _migrate_legacy_sourcing(db: Session, sheets: dict) -> None:
    """Apply migration 0009's mapping to data coming from an older backup.

    A backup taken before 0009 carries `make_or_buy` on the Items sheet — a column the schema
    no longer has, so the loader skips it — and pre-taxonomy values on DecidedCosts. Without
    this, restoring an old backup would silently undo the migration: the 14 item-level values
    would be dropped on the floor and "modified-buy" would reappear. Restoring must land in
    the same state the migration produces.
    """
    db.execute(
        text("UPDATE decided_costs SET make_or_buy = 'made-to-order' WHERE make_or_buy = 'modified-buy'")
    )
    items = sheets.get("Items")
    if items is None or "make_or_buy" not in [str(c) for c in items.columns]:
        return  # a current-schema backup: nothing legacy to fold in
    for rec in items.to_dict(orient="records"):
        value = rec.get("make_or_buy")
        item_id = rec.get("item_id")
        if not item_id or value is None or str(value).strip() in ("", "nan", "None"):
            continue
        value = "made-to-order" if str(value) == "modified-buy" else str(value)
        db.execute(
            text(
                "UPDATE decided_costs SET make_or_buy = :v "
                "WHERE item_id = :i AND (make_or_buy IS NULL OR make_or_buy = '')"
            ),
            {"v": value, "i": item_id},
        )
