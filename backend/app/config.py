"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Dev default: local SQLite file. Swap to managed Postgres later by setting
    # DATABASE_URL to postgresql+psycopg://... in .env — no code changes needed.
    database_url: str = "sqlite:///./zef_bom.dev.db"
    cors_origins: str = "http://localhost:5173"

    google_service_account_file: str = ""
    # Alternative to the file path: the full service-account JSON as a string (for hosts
    # like Railway where you can't commit a key file). Takes precedence over the file.
    google_service_account_json: str = ""
    drive_attachments_root_id: str = ""
    # Optional: explicit Drive folder for database backups. Blank = a "Backups" subfolder
    # is created under the attachments root automatically.
    drive_backups_folder_id: str = ""

    # "Sign in with Google" — the OAuth Web client ID; backend verifies ID tokens against it.
    google_oauth_client_id: str = ""
    # Secret for signing our own session JWTs. Set a strong value in prod.
    secret_key: str = "dev-insecure-secret-change-me-in-production-please"
    # Optional: restrict logins to a Workspace domain, e.g. "zef.energy". Blank = any.
    allowed_email_domain: str = ""
    # On first boot (empty users table), seed this email as the first admin.
    admin_email: str = ""

    auth_disabled: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
