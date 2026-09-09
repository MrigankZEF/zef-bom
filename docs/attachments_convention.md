# Attachments convention (Google Drive)

Attachments (quotes, datasheets, drawings, photos) live in Google Drive, not in the
database. The database stores only a **link**.

## Folder per part

A single top-level Drive folder — **`ZEF BOM Attachments`** — holds one subfolder per
part, named by `item_id`:

```
ZEF BOM Attachments/
├── AEC001A/
│   ├── 2026-04-12_quote_Schultz.pdf
│   ├── datasheet.pdf
│   └── drawing.dxf
├── AEC002A/
└── DAC001A/
```

- The backend (`app/drive.py`, M6) creates/locates the per-part subfolder when an
  item is first created or when a file is attached, then stores its URL in
  `items.drive_folder_url`.
- Cost evidence PDFs may additionally set `cost_evidence.attachment_url` to a specific
  file in that folder.
- The team can also just drop files into the folder via the Drive UI.
- One image per item can be **pinned as its thumbnail** (`items.thumbnail_file_id`), shown in
  the drawer's Key figures card. There is no image processing: Drive already stores a
  thumbnail for every format it can render, and the backend proxies those bytes because the
  `thumbnailLink` Drive hands out expires within hours and only works with our credentials.
  Pinned rather than "newest image in the folder", so a new upload never silently swaps the
  picture.
- **A file named after the item pins itself.** `AEC001A.png` in `AEC001A/` becomes that item's
  thumbnail with nobody having to open the drawer — an exact `<item_id>` filename is an
  explicit naming decision, not the newest-file guess the rule above exists to prevent. Two
  guards keep it that way: it fires only when nothing is pinned yet, and only on an exact stem
  match, case-insensitively, against `.png` / `.jpg` / `.jpeg` / `.webp`. An extension
  allowlist rather than "anything Drive can render", because Drive renders PDFs and
  `AEC001A.pdf` is a drawing. It happens on upload, and on the first listing of a folder — the
  team drops files straight into Drive, which never touches the upload endpoint, so listing is
  the only moment the backend learns those files exist. The history row is attributed to
  `auto (filename)` rather than to a person. `scripts/backfill_thumbnails.py` does the same
  pass over what is already in Drive (report-only without `--apply`).

## Config
- `DRIVE_ATTACHMENTS_ROOT_ID` — the Drive folder ID of `ZEF BOM Attachments`.
- `GOOGLE_SERVICE_ACCOUNT_FILE` — service-account key with access to that folder.
  Leave blank in dev to disable Drive calls.
