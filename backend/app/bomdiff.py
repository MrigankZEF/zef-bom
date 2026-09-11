"""What changed between two states of the same BOM.

Arithmetic over two known-good states, and **no history involved in the numbers**. That is
the point worth defending: a diff between a milestone and today needs no log at all, because
both sides are complete. `change_history` is asked for one thing only — who touched a row and
why — and a hole in it costs a sentence of provenance, never a figure. Building the numbers
out of the log would have made every gap in it a silently wrong total, and there were three
unlogged write paths until `71045fd`.

Two `BomGraph`s at the same tier, walked together. Both were built by the same code from the
same kind of rows (see `rows.py`), so a difference in the output is a difference in the BOM
rather than a difference in the arithmetic.

Rows split into two questions that are easy to conflate:

*Structure* — is the item in the BOM, and how many. Taken with `explode_boundaries=True`, so
the contents of a bought-in assembly are rows even while a supplier's single price is what
gets counted. Otherwise flipping an assembly from bought to built would report forty items
as "added" when the only thing that changed was one flag.

*Contribution* — what the item adds to the root's total: effective quantity × decided cost
for a costed leaf or a quoted assembly, plus its own process cost for an assembly we build.
That decomposition is exact — it is the same identity `audit_rollups.py` checks against
`rollup()` — so the contribution deltas sum to the change in the rolled-up total, and the
diff can say what accounts for the movement rather than merely that there was one.
"""
from __future__ import annotations

import datetime as _dt
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChangeHistory

# The per-item inputs a reader would call "a change". Deliberately not every column: a diff
# that flags `updated_at` on every row is a diff nobody reads twice.
#
# `cost_type_id` is in here because it is what an assembly's €/hour rate hangs off, and
# `assembly_minutes` because the two together are the labour cost. `covers` matters most of
# all — it decides whether a whole subtree counts.
ITEM_FIELDS = (
    "item_name", "item_type", "module_code", "material", "materials", "supplier",
    "supplier_country", "supplier_part_number", "lead_time_weeks", "weight_grams",
    "unit_of_measure", "cost_type_id", "external_reference",
)


def _fields(g, item_id: str) -> dict:
    """The comparable inputs for one item at the graph's tier."""
    it = g.items[item_id]
    est = g.decided.get((item_id, g.volume))
    t = g.labor.get(item_id)
    return {
        **{f: getattr(it, f, None) for f in ITEM_FIELDS},
        "unit_cost": est[1] if est is not None else None,
        "cost_min": est[0] if est is not None else None,
        "cost_max": est[2] if est is not None else None,
        "assembly_minutes": t[1] if t is not None else None,
        "covers": g.covers.get(item_id, "none"),
        "rate_eur_h": g.rates.get(it.cost_type_id) if it.cost_type_id is not None else None,
    }


def _state(g, root: str) -> dict[str, dict]:
    """`{item_id: {eff_qty, contribution, ...fields}}` for one side of the comparison."""
    eff: dict[str, float] = {}
    for d in (g.flatten_leaves(root, True), g.flatten_assemblies(root, True)):
        for k, v in d.items():
            eff[k] = eff.get(k, 0.0) + v

    contrib: dict[str, float] = {}
    for iid, q in g.flatten_leaves(root).items():
        est = g.decided.get((iid, g.volume))
        contrib[iid] = contrib.get(iid, 0.0) + (est[1] if est is not None else 0.0) * q
    for iid, q in g.flatten_assemblies(root).items():
        contrib[iid] = contrib.get(iid, 0.0) + g.assembly_cost(g.items[iid])[1] * q

    out: dict[str, dict] = {}
    for iid, q in eff.items():
        if iid not in g.items:
            continue
        out[iid] = {"eff_qty": q, "contribution": contrib.get(iid, 0.0), **_fields(g, iid)}
    return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _differs(a, b) -> bool:
    """Equality that does not report a change because 2 became 2.0.

    Floats get a tolerance rather than `!=`: quantities and costs pass through the same
    rounding a screen does, and a diff that fires on 1e-15 is noise that hides the real
    rows.
    """
    if _num(a) and _num(b):
        return abs(float(a) - float(b)) > 1e-9
    return a != b


def _annotations(db: Session, ids: set[str], since: datetime | None) -> dict[str, list[dict]]:
    """Who changed each item since the milestone, straight from the log.

    **Annotation only.** These rows explain the numbers above; they never produce them. A
    missing entry means the log has a hole — which is worth knowing and is not worth
    refusing to show a diff over.
    """
    if not ids or since is None:
        return {}
    # The bound is widened by a second in SQL and tightened again in Python, because SQLite
    # compares DATETIME as TEXT and the two sides are not written the same way: `func.now()`
    # stores whole seconds ("2026-09-10 13:50:20") while SQLAlchemy binds a Python datetime
    # with microseconds ("2026-09-10 13:50:20.000000"). The shorter string sorts FIRST, so a
    # row logged in the same second as the milestone failed `>=` and every annotation
    # silently vanished — on SQLite only, which is exactly the kind of difference that gets
    # found in production instead of here. The widened bound still uses the index.
    floor = since - _dt.timedelta(seconds=1)
    q = (
        select(ChangeHistory)
        .where(ChangeHistory.changed_at >= floor)
        .order_by(ChangeHistory.changed_at.desc(), ChangeHistory.id.desc())
    )
    out: dict[str, list[dict]] = {}
    for h in db.execute(q).scalars():
        if h.changed_at is not None and h.changed_at < since:
            continue
        # A bom_link's entity_id is "PARENT>CHILD"; attribute it to both ends, since either
        # row is where a reader would look for it.
        subjects = {h.entity_id.split(">")[0], h.entity_id.split(">")[-1]}
        for s in subjects & ids:
            bucket = out.setdefault(s, [])
            if len(bucket) < 8:  # enough to explain a row; not the item's whole history
                bucket.append({
                    "changed_at": h.changed_at.isoformat() if h.changed_at else None,
                    "changed_by": h.changed_by,
                    "entity_type": h.entity_type,
                    "field_changed": h.field_changed,
                    "old_value": h.old_value,
                    "new_value": h.new_value,
                    "change_reason": h.change_reason,
                })
    return out


def diff(
    *,
    before,
    after,
    root: str,
    db: Session | None = None,
    since: datetime | None = None,
    milestone: dict | None = None,
) -> dict:
    """The comparison. `before` and `after` are `BomGraph`s at the same tier."""
    if before.volume != after.volume:
        raise ValueError("a diff compares one tier, not two")

    b_state = _state(before, root) if root in before.items else {}
    a_state = _state(after, root) if root in after.items else {}

    rows = []
    for iid in sorted(set(b_state) | set(a_state)):
        b, a = b_state.get(iid), a_state.get(iid)
        if b is None:
            status, changed = "added", []
        elif a is None:
            status, changed = "removed", []
        else:
            changed = [
                {"field": k, "before": b.get(k), "after": a.get(k)}
                for k in ("eff_qty", *ITEM_FIELDS, "unit_cost", "cost_min", "cost_max",
                          "assembly_minutes", "covers", "rate_eur_h")
                if _differs(b.get(k), a.get(k))
            ]
            status = "changed" if changed else "unchanged"

        src = a or b
        name_of = (after if a is not None else before).items[iid].item_name
        rows.append({
            "item_id": iid,
            "item_name": name_of,
            "status": status,
            "eff_qty_before": None if b is None else round(b["eff_qty"], 3),
            "eff_qty_after": None if a is None else round(a["eff_qty"], 3),
            "unit_cost_before": None if b is None else b["unit_cost"],
            "unit_cost_after": None if a is None else a["unit_cost"],
            "contribution_before": round(0.0 if b is None else b["contribution"], 2),
            "contribution_after": round(0.0 if a is None else a["contribution"], 2),
            "contribution_delta": round(
                (0.0 if a is None else a["contribution"]) - (0.0 if b is None else b["contribution"]), 2
            ),
            "weight_before": None if b is None else b["weight_grams"],
            "weight_after": None if a is None else a["weight_grams"],
            "module_code": src["module_code"],
            "changed": changed,
        })

    # Biggest money movement first, then the rest alphabetically — a diff is read to find out
    # what moved the number, and an alphabetical list buries that.
    rows.sort(key=lambda r: (-abs(r["contribution_delta"]), r["item_id"]))

    br = before.rollup(root) if root in before.items else None
    ar = after.rollup(root) if root in after.items else None

    touched = {r["item_id"] for r in rows if r["status"] != "unchanged"}
    notes = _annotations(db, touched, since) if db is not None else {}

    counts = {k: 0 for k in ("added", "removed", "changed", "unchanged")}
    for r in rows:
        counts[r["status"]] += 1

    return {
        "root": root,
        "volume_tier": after.volume,
        "milestone": milestone,
        "counts": counts,
        "totals": {
            "cost_before": None if br is None else round(br.cost, 2),
            "cost_after": None if ar is None else round(ar.cost, 2),
            "cost_delta": round((0.0 if ar is None else ar.cost) - (0.0 if br is None else br.cost), 2),
            # The contribution deltas add up to `cost_delta` — same identity the rollup audit
            # checks — so a mismatch here is a bug in one of them, not a rounding quirk.
            "accounted_delta": round(sum(r["contribution_delta"] for r in rows), 2),
            "weight_before": None if br is None else round(br.weight_grams or 0.0, 1),
            "weight_after": None if ar is None else round(ar.weight_grams or 0.0, 1),
            "coverage_before": None if br is None else round(br.coverage, 4),
            "coverage_after": None if ar is None else round(ar.coverage, 4),
        },
        "rows": rows,
        "history": notes,
    }
