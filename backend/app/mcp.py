"""MCP: the same read-only view of the BOM, in the shape an AI connector expects.

Claude chat, the phone app and (increasingly) other assistants cannot run a shell, so the
"put the token in a file" route is closed to them. They talk to outside systems over MCP
instead: JSON-RPC 2.0 posted to one URL, with a list of named tools.

This is a thin shell over the read endpoints that already exist. Every tool calls the same
router function the HTTP API calls, so a number here cannot drift from the same number in
the web app -- there is no second implementation to keep in step.

READ ONLY, and structurally so. `TOOLS` below is the whole surface: if a name is not in that
dict there is no way to reach it, and every entry maps to a GET handler. A write endpoint
cannot be exposed here by accident, only by someone adding one on purpose -- and a test
fails if they do.
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from .routers import items as items_router
from .routers import tree as tree_router

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "zef-bom", "version": "0.1.0"}


def _summary(db: Session, volume: int = 100) -> Any:
    return tree_router.costing_summary(db=db, volume=volume)


def _breakdown(db: Session, root: str, volume: int = 100) -> Any:
    return tree_router.costing_breakdown(db=db, root=root, volume=volume)


def _tree(db: Session, root: str | None = None, volume: int = 100) -> Any:
    return tree_router.get_tree(db=db, root=root, volume=volume)


def _flat(db: Session, root: str | None = None, volume: int = 100) -> Any:
    return tree_router.get_flat(db=db, root=root, volume=volume)


def _pending(db: Session, module: str | None = None) -> Any:
    return tree_router.pending(db=db, module=module)


def _where_used(db: Session, item_id: str) -> Any:
    return tree_router.where_used(item_id=item_id, db=db)


def _search(db: Session, q: str | None = None, module: str | None = None,
            item_type: str | None = None, top_level_only: bool = False) -> Any:
    rows = items_router.list_items(db=db, module=module, item_type=item_type,
                                   top_level_only=top_level_only, q=q,
                                   include_archived=False)
    # The HTTP route hands these to a response_model; here nothing does, so pick the fields
    # a reader actually needs rather than serialising the whole ORM object.
    return [{"item_id": i.item_id, "item_name": i.item_name, "item_type": i.item_type,
             "module_code": i.module_code, "is_top_level": i.is_top_level,
             "supplier": i.supplier, "weight_grams": float(i.weight_grams) if i.weight_grams is not None else None}
            for i in rows]


def _item(db: Session, item_id: str) -> Any:
    i = items_router.get_item(item_id=item_id, db=db)
    return {"item_id": i.item_id, "item_name": i.item_name, "item_type": i.item_type,
            "module_code": i.module_code, "is_top_level": i.is_top_level,
            "material": i.material, "supplier": i.supplier,
            "supplier_country": i.supplier_country, "hs_code": i.hs_code,
            "country_of_origin": i.country_of_origin,
            "weight_grams": float(i.weight_grams) if i.weight_grams is not None else None,
            "comment": i.comment}


VOLUME = {"type": "integer", "enum": [1, 100, 10000],
          "description": "Plants per year. The BOM is priced at three volumes."}
ROOT = {"type": "string", "description": "Top-level BOM id, e.g. AEC066A."}

# name -> (handler, description, JSON-Schema of the arguments)
TOOLS: dict[str, tuple[Callable[..., Any], str, dict]] = {
    "costing_summary": (
        _summary,
        "Rolled-up cost of every top-level BOM at one volume, plus the cost-vs-volume curve. "
        "Start here for 'what does it cost'.",
        {"type": "object", "properties": {"volume": VOLUME}},
    ),
    "costing_breakdown": (
        _breakdown,
        "Every part inside one BOM with its effective quantity, cost and weight contribution, "
        "sorted by cost. Answers 'where does the money sit'.",
        {"type": "object", "properties": {"root": ROOT, "volume": VOLUME}, "required": ["root"]},
    ),
    "bom_tree": (
        _tree,
        "The BOM as a nested structure: what a thing is made of, all the way down.",
        {"type": "object", "properties": {"root": ROOT, "volume": VOLUME}},
    ),
    "flat_bom": (
        _flat,
        "The BOM flattened to one line per part with the total count one plant needs. "
        "This is the purchasing view.",
        {"type": "object", "properties": {"root": ROOT, "volume": VOLUME}},
    ),
    "pending": (
        _pending,
        "Parts and assemblies still missing data -- cost, weight, material, supplier country "
        "or assembly time. The review queue.",
        {"type": "object", "properties": {
            "module": {"type": "string", "description": "Optional module code, e.g. AEC."}}},
    ),
    "search_items": (
        _search,
        "Find parts and assemblies by code or name.",
        {"type": "object", "properties": {
            "q": {"type": "string", "description": "Text to match in the code or the name."},
            "module": {"type": "string"},
            "item_type": {"type": "string", "enum": ["part", "assembly"]},
            "top_level_only": {"type": "boolean"}}},
    ),
    "get_item": (
        _item,
        "One part or assembly: name, module, material, supplier, weight, HS code, origin.",
        {"type": "object", "properties": {
            "item_id": {"type": "string"}}, "required": ["item_id"]},
    ),
    "where_used": (
        _where_used,
        "Which assemblies a part appears in, and in what quantity.",
        {"type": "object", "properties": {
            "item_id": {"type": "string"}}, "required": ["item_id"]},
    ),
}


def tool_list() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]


class McpError(Exception):
    """A JSON-RPC error with its code, so the transport need not guess one."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def call_tool(db: Session, name: str, arguments: dict | None) -> Any:
    entry = TOOLS.get(name)
    if entry is None:
        raise McpError(-32602, f"Unknown tool: {name}")
    fn, _, schema = entry
    args = dict(arguments or {})
    allowed = set(schema.get("properties", {}))
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise McpError(-32602, f"{name} has no argument(s): {', '.join(unknown)}")
    missing = sorted(set(schema.get("required", [])) - set(args))
    if missing:
        raise McpError(-32602, f"{name} needs: {', '.join(missing)}")
    return fn(db, **args)
