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
- **M7** the COGS ladder + Facilities (backend landed; the input screen and the COGM/COGS
  views are still to build — see `docs/cogs/PLAN.md` build order steps 4-7)

## Costing, and the ladder on top of it

The BOM rolls up to a cost per plant at each of three volume tiers (@1 / @100 / @10k):
`rollup.cost = parts + assembly_cost`, where the assembly half is `AssemblyLabor` minutes x
the cost type's EUR/hour rate. An assembly marked `covers='all'` is a boundary — a supplier
quote replaces its whole subtree.

**The COGS ladder** (`backend/app/cogs.py`) adds four rungs on top of that number:

```
L1  BOM        material = rollup.cost - rollup.assembly_cost;  labour = rollup.assembly_cost
L2  Direct     rollup.cost / yield_factor + consumables + other
L3  COGM       + overhead pool / tier            <- the tier IS plants per year
L4  COGS       + freight + install + warranty (% of burdened)
```

Every figure is either read from the BOM or entered once on **Facilities** — a typed
facility (assembly / logistics / field) whose `kind` decides which rows its cost matrix has.
Nothing is entered twice, and nothing the BOM already knows is re-typed.

Two things worth knowing before reading any COGS number:

- **@1 is not "what a prototype costs."** It is "what a plant costs at a company producing
  one plant a year" — that plant absorbs a whole year of overhead. On the fixtures overhead
  is 84% of COGS at @1 and 19% at @10k. The divisor is named on screen for this reason.
- **Two BOMs' COGS figures cannot be added.** Every facility is fully allocated to whichever
  root is on screen, which is right for comparative simulation and makes the totals
  non-additive. There is no grand-total COGS endpoint, deliberately.

`python scripts/audit_cogs.py` re-derives all four rungs from the raw tables the long way and
checks the module agrees — the same relationship `audit_rollups.py` has to `rollups.py`.
