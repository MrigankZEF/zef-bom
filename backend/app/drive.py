"""Google Drive integration — folder-per-part attachments.

A service account (configured via GOOGLE_SERVICE_ACCOUNT_FILE) owns the access; it
must be a member of the root attachments folder (DRIVE_ATTACHMENTS_ROOT_ID). For each
part we keep a subfolder named by item_id, created on demand. Files (invoices, quotes,
datasheets) are uploaded into it.

If Drive isn't configured, `enabled()` is False and the endpoints return a clear 503.
"""
from __future__ import annotations

import io
from functools import lru_cache

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/drive"]


def enabled() -> bool:
    has_creds = bool(settings.google_service_account_json or settings.google_service_account_file)
    return bool(has_creds and settings.drive_attachments_root_id)


@lru_cache(maxsize=1)
def _service():
    """Build the Drive API client (cached). Creds come from the JSON env var if set,
    else the key file."""
    import json

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if settings.google_service_account_json:
        info = json.loads(settings.google_service_account_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
    return build("drive", "v3", credentials=creds, cache_discovery=False)

# Shared-drive support requires these flags on every call.
_SHARED = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


def ensure_part_folder(item_id: str) -> dict:
    """Find the part's subfolder under the root, creating it if missing. Returns {id, url}."""
    svc = _service()
    root = settings.drive_attachments_root_id
    safe = item_id.replace("'", "")
    q = (
        f"name = '{safe}' and '{root}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    res = svc.files().list(q=q, fields="files(id, webViewLink)", **_SHARED).execute()
    files = res.get("files", [])
    if files:
        f = files[0]
        return {"id": f["id"], "url": f.get("webViewLink") or _folder_url(f["id"])}
    created = svc.files().create(
        body={"name": item_id, "mimeType": "application/vnd.google-apps.folder", "parents": [root]},
        fields="id, webViewLink",
        **{"supportsAllDrives": True},
    ).execute()
    return {"id": created["id"], "url": created.get("webViewLink") or _folder_url(created["id"])}


def upload_file(item_id: str, filename: str, content: bytes, mimetype: str | None) -> dict:
    """Upload a file into the part's folder. Returns {id, name, url}."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    folder = ensure_part_folder(item_id)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype or "application/octet-stream", resumable=False)
    created = svc.files().create(
        body={"name": filename, "parents": [folder["id"]]},
        media_body=media, fields="id, name, webViewLink",
        **{"supportsAllDrives": True},
    ).execute()
    return {"id": created["id"], "name": created["name"],
            "url": created.get("webViewLink") or _file_url(created["id"]), "folder_url": folder["url"]}


def list_files(item_id: str) -> dict:
    """List files in the part's folder (empty if the folder doesn't exist yet)."""
    svc = _service()
    root = settings.drive_attachments_root_id
    safe = item_id.replace("'", "")
    q = f"name = '{safe}' and '{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    res = svc.files().list(q=q, fields="files(id, webViewLink)", **_SHARED).execute()
    folders = res.get("files", [])
    if not folders:
        return {"folder_url": None, "files": []}
    fid = folders[0]["id"]
    files = svc.files().list(
        q=f"'{fid}' in parents and trashed = false",
        fields="files(id, name, webViewLink, mimeType, modifiedTime, size)",
        orderBy="modifiedTime desc", **_SHARED,
    ).execute().get("files", [])
    return {
        "folder_url": folders[0].get("webViewLink") or _folder_url(fid),
        "files": [{"id": f["id"], "name": f["name"], "url": f.get("webViewLink"),
                   "mime": f.get("mimeType"), "modified": f.get("modifiedTime")} for f in files],
    }


def _folder_url(fid: str) -> str:
    return f"https://drive.google.com/drive/folders/{fid}"


def _file_url(fid: str) -> str:
    return f"https://drive.google.com/file/d/{fid}/view"
