"""
Miro OPML → BOM ingestion engine.

`miro_csv_fix.py` is a copy of the legacy `Miro_CSV_fix.py` (the read-only original
lives in `Bom Exploration 4`). In M2 it is adapted:
  * `InventoryAuthority` is rebuilt from the Postgres `items` table instead of Excel.
  * `write_database_records` is replaced by a writer for the new schema
    (items + bom_links keyed by item_id, plus change_history) — no item_revisions.
  * `build_review_csv` logic is wrapped to emit the diff JSON the Uploads UI consumes.
The pure parsing/resolution functions are reused verbatim.
"""
