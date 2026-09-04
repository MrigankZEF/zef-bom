"""Move URLs pasted into item notes over to the `item_links` table.

Before item_links existed, the notes field was the only place a supplier or shop URL fitted,
so that is where they went — one 300-character Amazon URL per note, overflowing the card and
telling nobody which link was which. This is the one-off cleanup.

For each item: every http(s) URL found in `comment` becomes an `item_links` row (kind guessed
from the host, see `_guess_type`) and is cut out of the note text. The leftover note is
tidied — collapsed blank lines, trimmed — and written back through `record_change`, so the
edit shows up in the item's history like any other.

Dry run is the default and prints every change. Nothing is written without `--apply`:

    python scripts/migrate_note_urls.py            # show what would change
    python scripts/migrate_note_urls.py --apply    # do it
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.history import record_change  # noqa: E402
from app.models import Item, ItemLink  # noqa: E402

# Trailing punctuation is part of the sentence, not the URL — a note routinely reads
# "see https://example.com/x." and the full stop must not end up in the href.
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
TRAILING = ".,;:!?)]}’\"'"

# Hosts we can name with confidence. Everything else falls back to 'info' rather than
# guessing "supplier" and putting a wrong label in front of a human.
SHOPS = (
    "amazon.", "mouser.", "digikey.", "digi-key.", "rs-online.", "conrad.", "farnell.",
    "reichelt.", "ebay.", "alibaba.", "aliexpress.", "distrelec.", "tme.eu", "bol.com",
)


def _clean(url: str) -> str:
    return url.rstrip(TRAILING)


def _guess_type(url: str, supplier: str | None) -> str:
    host = (urlparse(url).netloc or "").lower()
    path = (urlparse(url).path or "").lower()
    if supplier:
        # "TE Connectivity" → "te"; enough to match te.com without matching everything.
        token = re.sub(r"[^a-z0-9]", "", supplier.split()[0].lower())
        if token and len(token) >= 2 and token in host.replace("-", ""):
            return "supplier"
    if any(s in host for s in SHOPS):
        return "shop"
    if path.endswith(".pdf") or "datasheet" in path or "datasheet" in host:
        return "datasheet"
    return "info"


def _strip_urls(note: str, urls: list[str]) -> str:
    """Remove the URLs from the note and tidy what is left behind."""
    out = note
    for u in urls:
        out = out.replace(u, "")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n\s*\n\s*\n+", "\n\n", out)
    # A line that held nothing but a URL (possibly with a bullet or dash) is now noise.
    lines = [ln.rstrip() for ln in out.split("\n")]
    lines = [ln for ln in lines if ln.strip(" \t-*·:") != ""]
    return "\n".join(lines).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    moved = 0
    touched = 0
    try:
        existing = {
            (lk.item_id, lk.url)
            for lk in db.execute(select(ItemLink)).scalars()
        }
        for item in db.execute(select(Item).order_by(Item.item_id)).scalars():
            note = item.comment or ""
            urls = [_clean(u) for u in URL_RE.findall(note)]
            urls = [u for u in urls if u]
            if not urls:
                continue
            touched += 1
            new_note = _strip_urls(note, urls) or None
            print(f"\n{item.item_id}  {item.item_name}")
            for u in urls:
                kind = _guess_type(u, item.supplier)
                if (item.item_id, u) in existing:
                    print(f"    skip (already linked)  {kind:<12} {u[:90]}")
                    continue
                print(f"    link  {kind:<12} {u[:90]}")
                moved += 1
                if args.apply:
                    db.add(ItemLink(item_id=item.item_id, link_type=kind, url=u,
                                    created_by="migrate_note_urls"))
                    existing.add((item.item_id, u))
            print(f"    note  {(new_note or '(emptied)')[:120]}")
            if args.apply and new_note != item.comment:
                record_change(
                    db, entity_type="item", entity_id=item.item_id, change_type="update",
                    field_changed="comment", old_value=item.comment, new_value=new_note,
                    changed_by="migrate_note_urls",
                    change_reason="URLs moved to item_links",
                )
                item.comment = new_note
        if args.apply:
            db.commit()
    finally:
        db.close()

    print(f"\n{moved} link(s) from {touched} item(s)"
          + ("" if args.apply else " — dry run, nothing written. Re-run with --apply."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
