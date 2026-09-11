"""Pin `<item_id>.png` as an item's thumbnail, for the files already sitting in Drive.

`app/routers/attachments.py` does this from now on — on upload, and on the first listing of a
folder somebody filled in through the Drive UI. Neither helps the files that are in Drive
today: nothing pins them until a person happens to open that item's drawer. This walks every
item with a folder and does the same thing in one pass.

Same two guards as the live path, on purpose: it only ever pins where NOTHING is pinned, and
only on an exact `<item_id>.<image>` stem match. It will not overwrite a picture somebody chose
by hand, and it will not guess from the newest image in a folder.

    python scripts/backfill_thumbnails.py            # report only, writes nothing
    python scripts/backfill_thumbnails.py --apply    # pin them

Reads every folder it is given, so on a large catalogue it is one Drive call per item and slow.
That is fine for a job that wants running once.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app import drive  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Item  # noqa: E402
from app.routers.attachments import AUTO_PIN_BY, _THUMB_EXTS  # noqa: E402


def match(item_id: str, files: list[dict]) -> dict | None:
    want = item_id.lower()
    for f in files:
        stem, _, ext = (f.get("name") or "").lower().rpartition(".")
        if stem == want and f".{ext}" in _THUMB_EXTS:
            return f
    return None


def main(apply: bool) -> int:
    if not drive.enabled():
        print("Drive is not configured (GOOGLE_SERVICE_ACCOUNT_FILE + DRIVE_ATTACHMENTS_ROOT_ID).")
        return 2

    db = SessionLocal()
    from app.history import record_change

    items = list(db.execute(
        select(Item).where(Item.archived.is_(False), Item.thumbnail_file_id.is_(None))
    ).scalars())
    print(f"{len(items)} live items with no thumbnail pinned.\n")

    found = failed = 0
    for it in items:
        # No folder recorded and no folder in Drive means nothing to look at — `list_files`
        # returns an empty list rather than raising, so this stays quiet on the common case.
        try:
            data = drive.list_files(it.item_id, it.drive_folder_url)
        except Exception as exc:  # noqa: BLE001 — one unreadable folder must not end the run
            failed += 1
            print(f"  !! {it.item_id}: Drive lookup failed — {exc}")
            continue
        hit = match(it.item_id, data.get("files") or [])
        if hit is None:
            continue
        found += 1
        print(f"  {'pin ' if apply else 'would pin '}{it.item_id}  <-  {hit['name']}")
        if apply:
            it.thumbnail_file_id = hit["id"]
            record_change(
                db, entity_type="item", entity_id=it.item_id, change_type="update",
                field_changed="thumbnail_file_id", old_value=None, new_value=hit["id"],
                changed_by=AUTO_PIN_BY,
                change_reason=f"named after the item ({hit['name']}), backfilled",
            )

    if apply:
        db.commit()
    print(f"\n{found} matched{' and pinned' if apply else ''}"
          f"{f', {failed} folders unreadable' if failed else ''}.")
    if found and not apply:
        print("Nothing was written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
