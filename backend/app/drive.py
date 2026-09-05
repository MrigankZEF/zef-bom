"""Google Drive integration — folder-per-part attachments.

A service account (configured via GOOGLE_SERVICE_ACCOUNT_FILE) owns the access; it
must be a member of the root attachments folder (DRIVE_ATTACHMENTS_ROOT_ID). For each
part we keep a subfolder named by item_id, created on demand. Files (invoices, quotes,
datasheets) are uploaded into it.

If Drive isn't configured, `enabled()` is False and the endpoints return a clear 503.
"""
from __future__ import annotations

import io
import re
from functools import lru_cache

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/drive"]


def enabled() -> bool:
    has_creds = bool(settings.google_service_account_json or settings.google_service_account_file)
    return bool(has_creds and settings.drive_attachments_root_id)


@lru_cache(maxsize=1)
def _credentials():
    """Service-account creds (cached). From the JSON env var if set, else the key file."""
    import json

    from google.oauth2 import service_account

    if settings.google_service_account_json:
        info = json.loads(settings.google_service_account_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return service_account.Credentials.from_service_account_file(
        settings.google_service_account_file, scopes=SCOPES
    )


@lru_cache(maxsize=1)
def _service():
    """Build the Drive API client (cached)."""
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)

# Shared-drive support requires these flags on every call.
_SHARED = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}


def _folder_id_from_url(url: str | None) -> str | None:
    """Pull the Drive folder id out of a stored folder URL (…/drive/folders/<id>)."""
    if not url:
        return None
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _get_folder(fid: str | None) -> dict | None:
    """Fetch a live folder by id ({id, name, webViewLink}), or None if missing/trashed."""
    if not fid:
        return None
    try:
        f = _service().files().get(
            fileId=fid, fields="id, name, webViewLink, trashed, mimeType",
            **{"supportsAllDrives": True},
        ).execute()
    except Exception:  # noqa: BLE001 — a stale/deleted id just falls back to a name lookup
        return None
    if f.get("trashed") or f.get("mimeType") != "application/vnd.google-apps.folder":
        return None
    return f


def ensure_part_folder(item_id: str, folder_url: str | None = None) -> dict:
    """Find the part's folder, creating it if missing. Returns {id, url}.

    Prefer the item's *stored* folder, located by its stable id — so re-coding an item
    (AEC050A → UN050A) keeps it pointed at the same folder instead of hunting for one named
    after the new code (which would orphan the attachments). If the folder's name has drifted
    from the current code, rename it to match (best-effort) so Drive browsing stays tidy.
    Falls back to the original by-name find/create when there's no usable stored folder.
    """
    svc = _service()
    f = _get_folder(_folder_id_from_url(folder_url))
    if f:
        if f.get("name") != item_id:  # self-heal: keep the folder name in step with the code
            try:
                svc.files().update(
                    fileId=f["id"], body={"name": item_id}, **{"supportsAllDrives": True}
                ).execute()
            except Exception:  # noqa: BLE001 — cosmetic only; never block on it
                pass
        return {"id": f["id"], "url": f.get("webViewLink") or _folder_url(f["id"])}

    root = settings.drive_attachments_root_id
    safe = item_id.replace("'", "")
    q = (
        f"name = '{safe}' and '{root}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    res = svc.files().list(q=q, fields="files(id, webViewLink)", **_SHARED).execute()
    files = res.get("files", [])
    if files:
        hit = files[0]
        return {"id": hit["id"], "url": hit.get("webViewLink") or _folder_url(hit["id"])}
    created = svc.files().create(
        body={"name": item_id, "mimeType": "application/vnd.google-apps.folder", "parents": [root]},
        fields="id, webViewLink",
        **{"supportsAllDrives": True},
    ).execute()
    return {"id": created["id"], "url": created.get("webViewLink") or _folder_url(created["id"])}


def upload_file(item_id: str, filename: str, content: bytes, mimetype: str | None,
                folder_url: str | None = None) -> dict:
    """Upload a file into the part's folder. Returns {id, name, url}."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    folder = ensure_part_folder(item_id, folder_url)
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


def upload_file_nested(item_id: str, rel_dir: str, filename: str, content: bytes, mimetype: str | None,
                       folder_url: str | None = None) -> dict:
    """Upload a file into the part's folder, re-creating a sub-folder path (e.g.
    'membranes/typeA') so a whole folder structure can be uploaded at once."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = _service()
    folder = ensure_part_folder(item_id, folder_url)
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


def list_files(item_id: str, folder_url: str | None = None) -> dict:
    """List files in the part's folder (empty if the folder doesn't exist yet).

    Located by the stored folder id when available — so it survives re-codes — otherwise by a
    name lookup on the current code."""
    svc = _service()
    folder = _get_folder(_folder_id_from_url(folder_url))
    if folder is None:
        root = settings.drive_attachments_root_id
        safe = item_id.replace("'", "")
        q = f"name = '{safe}' and '{root}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res = svc.files().list(q=q, fields="files(id, webViewLink)", **_SHARED).execute()
        folders = res.get("files", [])
        if not folders:
            return {"folder_url": None, "files": []}
        folder = folders[0]
    fid = folder["id"]
    files = svc.files().list(
        q=f"'{fid}' in parents and trashed = false",
        fields="files(id, name, webViewLink, mimeType, modifiedTime, size, thumbnailLink)",
        orderBy="modifiedTime desc", **_SHARED,
    ).execute().get("files", [])
    return {
        "folder_url": folder.get("webViewLink") or _folder_url(fid),
        "files": [{"id": f["id"], "name": f["name"], "url": f.get("webViewLink"),
                   "mime": f.get("mimeType"), "modified": f.get("modifiedTime"),
                   # Drive only generates one for formats it can render — the UI offers
                   # "set as thumbnail" on exactly those files.
                   "has_thumbnail": bool(f.get("thumbnailLink"))} for f in files],
    }


def thumbnail(file_id: str, size: int = 320) -> tuple[bytes, str] | None:
    """Drive's own thumbnail for a file, as (bytes, mime). None if it has none.

    Drive generates and stores a thumbnail for every format it can render, so there is
    nothing to resize here. The `thumbnailLink` it hands out expires within hours and is
    tied to our credentials, which is why it is fetched server-side and never given to the
    browser — the browser asks our endpoint instead.
    """
    try:
        meta = _service().files().get(
            fileId=file_id, fields="thumbnailLink, mimeType, trashed",
            **{"supportsAllDrives": True},
        ).execute()
    except Exception:  # noqa: BLE001 — a deleted or unshared file simply has no thumbnail
        return None
    link = meta.get("thumbnailLink")
    if meta.get("trashed") or not link:
        return None
    # The link ends in a size hint (…=s220); ask for the size we actually display.
    link = re.sub(r"=s\d+$", f"=s{size}", link)
    from google.auth.transport.requests import AuthorizedSession

    try:
        res = AuthorizedSession(_credentials()).get(link, timeout=20)
        if res.status_code != 200 or not res.content:
            return None
        return res.content, res.headers.get("Content-Type", "image/jpeg")
    except Exception:  # noqa: BLE001
        return None


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
