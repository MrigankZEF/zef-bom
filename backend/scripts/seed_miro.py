"""Ingest a Miro OPML export into the BOM database.

Usage (from backend/, with the venv active):
    python scripts/seed_miro.py path/to/export.opml --top-level --user you@zef.energy

This is the same engine the Uploads tab uses (M5); the script is just a CLI front door
for the first import / replays.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `app` importable when run as `python scripts/seed_miro.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bom_ingest.service import ingest_opml_file  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a Miro OPML into the BOM database.")
    parser.add_argument("opml", type=Path, help="Path to the OPML export.")
    parser.add_argument("--user", default="seed", help="Recorded as uploaded_by / changed_by.")
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--top-level", action="store_true", help="Mark the OPML root(s) as top-level BOM(s)."
    )
    parser.add_argument(
        "--no-apply", action="store_true", help="Parse + record the batch but don't write items."
    )
    args = parser.parse_args()

    if not args.opml.exists():
        print(f"File not found: {args.opml}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        result = ingest_opml_file(
            db,
            args.opml,
            uploaded_by=args.user,
            notes=args.notes,
            is_top_level=args.top_level,
            auto_apply=not args.no_apply,
        )
    finally:
        db.close()

    counts = result["diff"]["counts"]
    print(f"Batch {result['batch_id']} — status: {result['status']}")
    print(
        f"  unique items: {counts['unique_items']} "
        f"({counts['allocated']} auto-numbered, {counts['pre_numbered']} pre-numbered in Miro) · "
        f"conflicts: {counts['conflicts']} · needs review: {counts['needs_review']}"
    )
    if result["applied"]:
        a = result["applied"]
        print(f"  applied: +{a['items_created']} items, +{a['links_created']} links")
    if result["blockers"]:
        print(f"  {result['blockers']} cell(s) need review — open the Uploads diff to resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
