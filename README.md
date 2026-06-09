# ZEF BOM / Inventory / Costing

Next-generation BOM tool for the ZEF microplant: browse the hierarchy, fill in
per-item data, review Miro imports, and read rolled-up cost/weight at volume
scenarios — backed by Postgres with a full audit trail and Drive attachments.

> Full plan: `C:\Users\mriga\.claude\plans\compiled-wiggling-quiche.md`
> Legacy reference (read-only): `G:\Shared drives\BOM\Bom Exploration 4`

## Stack

| Layer | Tech |
|---|---|
| Database | Managed **Postgres** (provider chosen at build time) |
| Backend | **FastAPI** + SQLAlchemy 2.0 + Alembic; reuses the legacy Miro ingestion engine |
| Frontend | **Vite + React** (ports the prototype UI + ZEF design system) |
| Attachments | **Google Drive** — folder per part |

## ⚠️ This repo lives on a Google Drive shared drive

Source files sync fine, but **`node_modules/`, a Python `.venv/`, and `.git/` churn
Drive sync** (thousands of small files). Recommended workflow:

- **Develop from a local clone**, not directly on `G:\`. Push/pull through git.
- `node_modules`, `.venv`, and `.env` are git-ignored and should never be synced.

## Layout

```
backend/    FastAPI app, SQLAlchemy models, Alembic migrations, ingestion engine, seed scripts
frontend/   Vite + React app
docs/        data model, API, attachments convention, carried-over principles
```

## Backend — getting started

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate     # (local, NOT on the G: drive)
pip install -e ".[dev]"
copy .env.example .env                              # then set DATABASE_URL
alembic upgrade head                                # create all tables
python scripts/seed_inventory.py                    # M1: load ~317 legacy parts
python scripts/seed_miro.py seed/mindmap.opml       # M2: load the hierarchy
uvicorn app.main:app --reload                       # http://localhost:8000/docs
```

Schema changes later: edit `app/models.py`, then
`alembic revision --autogenerate -m "..."` → `alembic upgrade head`.
Backups are handled by the managed Postgres provider; `scripts/backup_to_drive.py`
adds a secondary `pg_dump` → Drive mirror.

## Frontend — getting started

```bash
cd frontend
npm install
copy .env.example .env       # VITE_API_BASE defaults to /api (proxied to :8000)
npm run dev                  # http://localhost:5173
```

## Milestones

- **M0** scaffold + schema + Alembic ← *current*
- **M1** seed legacy inventory → `items`
- **M2** adapt ingestion engine + seed Miro hierarchy → `bom_links`
- **M3** read API + Browse tree + drawer + rollups (usable read-only MVP)
- **M4** edit path + two-layer cost model + change_history
- **M5** uploads diff + approve
- **M6** costing, pending, history, Drive attachments, auth/roles
