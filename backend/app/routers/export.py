"""Export a top-level BOM as OPML (Miro round-trip) or CSV (spreadsheets).

OPML node text matches the import grammar — ``CODE: name`` with ``#qty`` appended
when quantity ≠ 1 — so an exported file re-imports cleanly. CSV is a flat, indented
BOM explosion (one row per node occurrence in the tree).
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from xml.dom import minidom

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AssemblyLabor, DecidedCost
from ..rollups import BomGraph

router = APIRouter(tags=["export"])

TIERS = [1, 100, 10000]


def _tier_label(v: int) -> str:
    return f"{v // 1000}k" if v >= 1000 else str(v)


def _node_text(item, qty: float) -> str:
    text = f"{item.item_id}: {item.item_name}"
    if qty and float(qty) != 1:
        text += f" #{int(qty)}"
    return text


@router.get("/export/opml")
def export_opml(root: str = Query(...), db: Session = Depends(get_db)):
    g = BomGraph(db)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")

    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = g.items[root].item_name
    body = ET.SubElement(opml, "body")

    def build(item_id: str, qty: float, parent_el: ET.Element, seen: frozenset[str]) -> None:
        el = ET.SubElement(parent_el, "outline", text=_node_text(g.items[item_id], qty))
        if item_id in seen:  # cycle guard
            return
        for child, q in g.children.get(item_id, []):
            if child in g.items:
                build(child, q, el, seen | {item_id})

    build(root, 1, body, frozenset())
    xml = minidom.parseString(ET.tostring(opml, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    return Response(
        content=xml,
        media_type="text/x-opml",
        headers={"Content-Disposition": f'attachment; filename="{root}.opml"'},
    )


@router.get("/export/csv")
def export_csv(root: str = Query(...), db: Session = Depends(get_db)):
    g = BomGraph(db)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")

    decided: dict[tuple[str, int], float] = {
        (dc.item_id, dc.volume_tier): float(dc.unit_cost_eur)
        for dc in db.execute(select(DecidedCost)).scalars()
    }
    # Costing basis per (item, tier). Loaded across all three tiers rather than taken from
    # `g`, which only ever holds one — a harness built in house at @1 and quoted at @10k is
    # a different row in each cost column.
    covers: dict[tuple[str, int], str] = {
        (al.item_id, al.volume_tier): al.covers
        for al in db.execute(select(AssemblyLabor)).scalars()
        if al.covers != "none"
    }

    def is_boundary(item_id: str, tier: int) -> bool:
        return bool(g.children.get(item_id)) and covers.get((item_id, tier)) == "all"

    def per_tier(vals: dict[int, str]) -> str:
        """Collapse a per-tier value to one cell: bare when every tier agrees."""
        live = {t: v for t, v in vals.items() if v}
        if not live:
            return ""
        if len(live) == len(TIERS) and len(set(live.values())) == 1:
            return next(iter(live.values()))
        return " ".join(f"{_tier_label(t)}:{live[t]}" for t in TIERS if t in live)

    rows: list[dict] = []

    def walk(
        item_id: str, qty: float, parent_id: str | None, level: int,
        seen: frozenset[str], boxed: dict[int, str],
    ) -> None:
        """`boxed` maps a tier to the nearest bought-in ancestor at that tier, if any."""
        it = g.items[item_id]
        is_leaf = not g.children.get(item_id)

        def cost_for(tier: int):
            # Anything inside a supplier's scope costs us nothing at that tier — the price
            # is already on the ancestor, so the column still sums to the BOM total.
            if tier in boxed:
                return ""
            if is_leaf or is_boundary(item_id, tier):
                return decided.get((item_id, tier), "")
            return ""   # an ordinary assembly is derived, not a number of its own

        rows.append({
            "level": level,
            "item_id": item_id,
            "item_name": it.item_name,
            "type": it.item_type,
            "module": it.module_code or "",
            "qty_in_parent": int(qty) if qty else 1,
            "parent_id": parent_id or "",
            "weight_g": it.weight_grams if it.weight_grams is not None else "",
            "supplier": it.supplier or "",
            "country": it.supplier_country or "",
            "material": ", ".join(it.materials) if it.materials else (it.material or ""),
            "covers": per_tier({t: covers.get((item_id, t), "") for t in TIERS}),
            "included_in": per_tier({t: boxed.get(t, "") for t in TIERS}),
            "cost_eur@1": cost_for(1),
            "cost_eur@100": cost_for(100),
            "cost_eur@10k": cost_for(10000),
        })
        if item_id in seen:
            return
        # A boundary boxes everything below it from here down, per tier.
        child_boxed = {**boxed, **{t: item_id for t in TIERS if t not in boxed and is_boundary(item_id, t)}}
        for child, q in g.children.get(item_id, []):
            if child in g.items:
                walk(child, q, item_id, level + 1, seen | {item_id}, child_boxed)

    walk(root, 1, None, 0, frozenset(), {})

    cols = ["level", "item_id", "item_name", "type", "module", "qty_in_parent", "parent_id",
            "weight_g", "supplier", "country", "material", "covers", "included_in",
            "cost_eur@1", "cost_eur@100", "cost_eur@10k"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{root}.csv"'},
    )


# ── assembly flow chart (Mermaid, wrapped in Markdown) ────────────────────────
# The BOM tree read as a production plan: every assembly becomes a station, every
# leaf part an input feeding it, and every arrow points downstream at the finished
# product. Stations are numbered in post-order, so S01 is the first thing built and
# the highest number is final assembly — the fishbone spine runs left to right.

_MERMAID_CLASSES = """
    classDef product fill:#1C1B1A,stroke:#000000,color:#FFFFFF,stroke-width:2px;
    classDef station fill:#E6E1D8,stroke:#3D5A6B,color:#1C1B1A,stroke-width:1.5px;
    classDef part fill:#F9F7F3,stroke:#C9C2B8,color:#3D3B38;
    classDef supplied fill:#F9F7F3,stroke:#3D5A6B,color:#3D5A6B,stroke-width:1.5px,stroke-dasharray:5 3;
"""


def _node_id(item_id: str) -> str:
    """Mermaid ids allow only word characters — ZEF-1000.2A → N_ZEF_1000_2A."""
    return "N_" + "".join(c if c.isalnum() else "_" for c in item_id)


def _mermaid_label(text: str) -> str:
    """Escape a name for a quoted Mermaid label.

    A label is rendered as HTML, so an angle bracket in a name would be swallowed as a
    tag; ``#…;`` is Mermaid's entity syntax, and a bare quote closes the label early.
    The ``<br/>`` separators are added after this, on already-escaped pieces.
    """
    return (
        (text or "")
        .replace("#", "#35;")   # first — the escapes below introduce their own #
        .replace('"', "#quot;")
        .replace("<", "#lt;")
        .replace(">", "#gt;")
        .replace("\n", " ")
        .strip()
    )


def _cell(text: str) -> str:
    """A pipe inside a name would split the Markdown table column."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _qty(q: float) -> str:
    f = float(q or 1)
    return str(int(f)) if f == int(f) else f"{f:g}"


@router.get("/export/flowchart")
def export_flowchart(root: str = Query(...), db: Session = Depends(get_db)):
    g = BomGraph(db)
    if root not in g.items:
        raise HTTPException(404, f"Item {root} not found")

    # The two charts are the same graph seen at two zoom levels, so the walk records
    # nodes and links once and each chart is a filter over them.
    order: list[str] = []          # declaration order — children before parents
    decls: dict[str, str] = {}     # item_id → its Mermaid node declaration
    kind: dict[str, str] = {}      # item_id → station | supplied | part
    links: list[tuple[str, str, float]] = []   # child, parent, qty
    stations: list[tuple[int, str, list[tuple[str, str, str]]]] = []  # no, item_id, inputs
    visited: dict[str, str] = {}   # item_id → mermaid node id (an item shared by two
                                   # parents is one station feeding both, not a copy)
    counter = [0]

    supplied: list[str] = []       # bought-in assemblies: drawn, but never built here

    def visit(item_id: str, path: frozenset[str]) -> str:
        if item_id in visited:
            return visited[item_id]
        it = g.items[item_id]
        nid = _node_id(item_id)
        # A bought-in assembly arrives finished. Its contents are the supplier's production
        # plan, not ours, so the chart stops at the box it comes in — drawing its internals
        # as stations would put work on our floor that nobody here does.
        boundary = item_id != root and g.is_boundary(item_id)
        # A cycle would recurse forever; draw the node once and stop descending.
        kids = [] if (item_id in path or boundary) else [
            (c, q) for c, q in g.children.get(item_id, []) if c in g.items
        ]
        children = [(visit(c, path | {item_id}), q, c) for c, q in kids]
        visited[item_id] = nid

        label = f"{_mermaid_label(item_id)}<br/>{_mermaid_label(it.item_name)}"
        order.append(item_id)
        if kids:
            counter[0] += 1
            no = counter[0]
            verb = "Final assembly" if item_id == root else "Assemble"
            cls = "product" if item_id == root else "station"
            shape = ('(["', '"])') if item_id == root else ('[["', '"]]')
            kind[item_id] = "station"
            decls[item_id] = f'    {nid}{shape[0]}S{no:02d} · {verb}<br/>{label}{shape[1]}:::{cls}'
            stations.append((no, item_id, [
                (c, _cell(g.items[c].item_name), _qty(q)) for _, q, c in children
            ]))
        elif boundary:
            inside = len(g.descendants(item_id))
            kind[item_id] = "supplied"
            decls[item_id] = (
                f'    {nid}[/"Supplied<br/>{label}<br/>{inside} item{"s" if inside != 1 else ""} inside"/]:::supplied'
            )
            supplied.append(item_id)
        else:
            kind[item_id] = "part"
            decls[item_id] = f'    {nid}("{label}"):::part'

        for _cid, q, c in children:
            links.append((c, item_id, q))
        return nid

    visit(root, frozenset())

    def chart(keep) -> list[str]:
        """One fenced Mermaid block over the nodes `keep` accepts, and nothing else.

        A link survives only when both ends do, so dropping the loose parts leaves the
        station-to-station spine intact instead of dangling arrows.
        """
        kept = [i for i in order if keep(i)]
        in_chart = set(kept)
        out = ["```mermaid", "flowchart LR", *(decls[i] for i in kept), ""]
        for child, parent, q in links:
            if child in in_chart and parent in in_chart:
                arrow = f'-->|"× {_qty(q)}"|' if float(q or 1) != 1 else "-->"
                out.append(f"    {_node_id(child)} {arrow} {_node_id(parent)}")
        out += [_MERMAID_CLASSES.rstrip("\n"), "```", ""]
        return out

    it = g.items[root]
    title = f"{root} — {(it.item_name or '').strip()}"
    part_count = sum(1 for k in kind.values() if k == "part")
    line_count = sum(1 for k in kind.values() if k in ("station", "supplied"))

    lines = [
        f"# Assembly flow — {title}",
        "",
        f"{len(stations)} assembly station{'s' if len(stations) != 1 else ''}, "
        f"{part_count} distinct part{'s' if part_count != 1 else ''}"
        + (f", {len(supplied)} bought-in assembl{'ies' if len(supplied) != 1 else 'y'}" if supplied else "")
        + ". Every arrow runs downstream towards the finished product; an edge label is the "
        "quantity of that input per unit of the station it feeds. Stations are numbered in "
        "build order — S01 first, the highest number is final assembly.",
        "",
        *(([
            "A dashed node arrives finished from a supplier. Its contents are in the BOM for "
            "reference, but they are built on someone else's floor, so no station is drawn "
            f"for them: {', '.join('`' + s + '`' for s in supplied)}.",
            "",
        ]) if supplied else []),
        "Two diagrams follow. Each sits alone in a fenced Mermaid block — everything "
        "between the fence lines pastes straight into mermaid.live, a Markdown viewer or a "
        "Miro Mermaid widget, and no line outside a fence belongs to a diagram.",
        "",
        "---",
        "",
        "## 1 · Full production flow",
        "",
        f"Every input in the tree: {len(stations)} station{'s' if len(stations) != 1 else ''} "
        f"with all {part_count} part{'s' if part_count != 1 else ''} feeding in. This is the "
        "complete picture and the busiest of the two.",
        "",
        *chart(lambda i: True),
        "---",
        "",
        "## 2 · Assembly line only",
        "",
        f"The same flow with the loose parts stripped out — {line_count} block"
        f"{'s' if line_count != 1 else ''}, station to station. It answers what each station "
        "hands the next one; a station with no arrow coming in is fed only by parts and can "
        "start on day one.",
        "",
        *chart(lambda i: kind[i] in ("station", "supplied")),
        "---",
        "",
        "## Stations, in build order",
        "",
        "| Station | Assembly | Inputs |",
        "| --- | --- | --- |",
    ]
    for no, sid, inputs in stations:
        ins = "<br/>".join(f"{q} × {cid} {name}" for cid, name, q in inputs) or "—"
        lines.append(f"| S{no:02d} | `{sid}` {_cell(g.items[sid].item_name)} | {ins} |")
    lines.append("")

    return Response(
        content="\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{root}-assembly-flow.md"'},
    )
