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


def _ensure_child_folder(name: str, parent_id: str) -> str:
    """Find/create a sub-folder by name under parent_id; return its id."""
    svc = _service()
    safe = name.replace("'", "")
    q = (f"name = '{safe}' and '{parent_id}' in parents "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    files = svc.files().list(q=q, fields="files(id)", **_SHARED).execute().get("files", [])
    if files:
        return files[0]["id"]
    return svc.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id", **{"supportsAllDrives": True},
    ).execute()["id"]


def upload_file_nested(item_id: str, rel_dir: str, filename: str, content: bytes, mimetype: str | None) -> dict:
    """Upload a file into the part's folder, re-creating a sub-folder path (e.g.
    'membranes/typeA') so a whole folder structure can be uploaded at once."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    folder = ensure_part_folder(item_id)
    parent = folder["id"]
    for seg in (s.strip() for s in (rel_dir or "").replace("\\", "/").split("/")):
        if seg and seg not in (".", ".."):
            parent = _ensure_child_folder(seg, parent)
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype or "application/octet-stream", resumable=False)
    created = svc.files().create(
        body={"name": filename, "parents": [parent]},
        media_body=media, fields="id, name, webViewLink", **{"supportsAllDrives": True},
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


# ── database backups (a "Backups" subfolder under the attachments root) ──────
BACKUPS_FOLDER_NAME = "Backups"


@lru_cache(maxsize=1)
def ensure_backups_folder() -> str:
    """Find (or create) the Backups folder and return its id. Uses an explicit
    DRIVE_BACKUPS_FOLDER_ID if set, else a 'Backups' subfolder of the attachments root."""
    if settings.drive_backups_folder_id:
        return settings.drive_backups_folder_id
    svc = _service()
    root = settings.drive_attachments_root_id
    q = (
        f"name = '{BACKUPS_FOLDER_NAME}' and '{root}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    files = svc.files().list(q=q, fields="files(id)", **_SHARED).execute().get("files", [])
    if files:
        return files[0]["id"]
    created = svc.files().create(
        body={"name": BACKUPS_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder", "parents": [root]},
        fields="id", **{"supportsAllDrives": True},
    ).execute()
    return created["id"]


def upload_backup(filename: str, content: bytes, mimetype: str) -> dict:
    """Upload a backup workbook into the Backups folder. Returns {id, name, url}."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    folder = ensure_backups_folder()
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mimetype, resumable=False)
    created = svc.files().create(
        body={"name": filename, "parents": [folder]},
        media_body=media, fields="id, name, webViewLink", **{"supportsAllDrives": True},
    ).execute()
    return {"id": created["id"], "name": created["name"],
            "url": created.get("webViewLink") or _file_url(created["id"])}


def list_backups() -> list[dict]:
    """List backup files in the Backups folder, newest first."""
    svc = _service()
    folder = ensure_backups_folder()
    return svc.files().list(
        q=f"'{folder}' in parents and trashed = false",
        fields="files(id, name, webViewLink, createdTime, size)",
        orderBy="createdTime desc", **_SHARED,
    ).execute().get("files", [])


def trash_file(file_id: str) -> None:
    """Move a Drive file to trash (used by backup retention)."""
    _service().files().update(fileId=file_id, body={"trashed": True}, **{"supportsAllDrives": True}).execute()


def _folder_url(fid: str) -> str:
    return f"https://drive.google.com/drive/folders/{fid}"


def _file_url(fid: str) -> str:
    return f"https://drive.google.com/file/d/{fid}/view"
