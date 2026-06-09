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

## Config
- `DRIVE_ATTACHMENTS_ROOT_ID` — the Drive folder ID of `ZEF BOM Attachments`.
- `GOOGLE_SERVICE_ACCOUNT_FILE` — service-account key with access to that folder.
  Leave blank in dev to disable Drive calls.
