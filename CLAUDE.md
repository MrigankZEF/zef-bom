# ZEF BOM

BOM, inventory and costing for the ZEF microplant. FastAPI + SQLAlchemy behind a React
frontend that the backend serves itself, on Railway with managed Postgres. SQLite locally.

People quote the numbers this tool produces. That is the constraint everything else follows
from.

## The invariant

Rolled-up cost of AEC Standalone, at the three volume tiers:

```
@1       EUR 30,866.81
@100     EUR  5,576.56
@10,000  EUR  2,314.09
```

**Check this before and after any change that could touch costing**, against a database
rebuilt from a production backup — not against the dev database, which has drifted. If a
change moves these, either the change is wrong or the change is the point; there is no third
case, and the difference must be understood before it ships.

These figures are a tripwire, not a constant. Real work on the data moves them, and when it
does they are re-based deliberately, with the reason written down — never quietly edited to
whatever the suite now prints, which would turn the check into a mirror.

They last moved in September 2026, from `30,372.41 / 7,960.64 / 2,871.87`, for three reasons,
all of them legitimate:

* Placeholder prices on the cell components were replaced with real ones — Cell Membrane
  5.00 → 1.80 and Bipolar Plate 5.00 → 2.50 at @100, each used in the hundreds. The single
  largest factor.
* Half Stack Cells went from 26 cells to 22, so the plant has 48 fewer. A design change, not
  a costing one.
* Nine assemblies that had no cost type — Stack, Cell, Midplate, Cell Frame, Half Stack
  Cells, End Cell, Core among them — were given one and timed. They had been contributing
  zero and are now priced, which is why coverage reads 100% rather than 93%.

Quotes were also cleared off five assemblies around the same time. That changed no number:
an assembly with children is costed from its children, so a price sitting on one is never
read unless `covers='all'` makes it a boundary. Worth knowing before concluding that deleting
one did something.

`backend/scripts/audit_rollups.py` recomputes every cost two further ways, sharing no code
with `rollups.py`. It exists to check the graph, not to trust it: if it disagrees with the
engine, do not teach it the engine's answer.

## Running it

```bash
cd backend && python -m pytest -q          # the suite
cd backend && python -m alembic upgrade head
cd frontend && npm install && npm run build
```

The built frontend is committed under `backend/webapp/` and is what production serves, so a
frontend change is not live until that is rebuilt and committed. Copy `frontend/dist/` over
`backend/webapp/`, strip carriage returns, and check the diff is only the bundle hash.

## Rules that came from real incidents

- **Never derive a value by subtracting rounded values.** Send it explicitly from the source.
  A displayed assembly cost was 6.4% wrong this way.
- **A missing input shows as `—`, never `0.00`**, and a total built on one is a floor, not a
  price. Say so on screen.
- **Nothing is hard-deleted.** Everything archives; every mutation writes to `change_history`.
- **Every new table joins the backup and restore lists** in `backend/app/backup.py`. The one
  deliberate exception is `api_tokens`, and the reasoning is written there.
- **Migrations are a chain.** Each names its predecessor; they cannot be reordered. Additive
  by default. Before anything irreversible, write down what it destroys.
- **Allow-list URL schemes, never deny-list.** `javascript://%0a…` once matched a `scheme://`
  check and became stored XSS.
- **Do not put a working repo on Google Drive.** It has eaten `node_modules` and corrupted git
  objects. This one lives there for sharing; clone elsewhere to work.

## Reading the BOM from an assistant

There is a read-only HTTP API, and an MCP connector at `/api/mcp`. Both take the same token,
both refuse every write, and both are described in `docs/connect-an-ai.md`. The curated schema
for AI connectors is `docs/api/zef-bom-readonly-openapi.json` — ten GET endpoints, kept in step
with the app by a test.

If a token is available on this machine it is at `~/.zefbom-token`, and the API is at
`https://zef-bom.up.railway.app`. Send it as `Authorization: Bearer <token>`. A token can only
ever read; editing needs a browser sign-in.

## Layout

```
backend/app/         rollups.py (the cost graph), routers/, models.py, auth.py, mcp.py
backend/alembic/     migrations, in a strict chain
backend/tests/       one suite, run it all
frontend/src/        React; zef.css holds the design tokens and the rules they follow
docs/                data model, validation rules, the COGS plan, the AI guide
```

`frontend/src/styles/zef.css` is the design system and is not decoration: warm bone ground,
charcoal ink, one red accent, hairlines. No gradients, no drop shadows. Read it before
changing how anything looks.
