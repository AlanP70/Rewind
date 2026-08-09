# Rewind

A longitudinal learning archive. You upload course material, ask *"where did I
first learn recursion?"*, and get a chronological timeline of every place that
concept appeared — first occurrence marked, each hit linking to the exact page
and passage in the source.

- **`ARCHITECTURE.md`** — schema, and the reasoning behind every column.
- **`ROADMAP.md`** — the phase plan and the constraints settled per phase.
- **`CLAUDE.md`** — the invariants that are not up for renegotiation.

**Current phase: 0 — an empty app deployed end to end.** There is no product
yet. What runs is two health routes, one migration, and one page that displays
the result. That is the whole point: the deployment is debugged while there is
nothing else that could be broken.

## Prerequisites

| | |
|---|---|
| Docker Desktop | runs Postgres and Redis |
| Node 20+ | `node --version` |
| [uv](https://docs.astral.sh/uv/) | provisions Python 3.12 |

**Do not use the system Python.** `backend/pyproject.toml` pins `==3.12.*` and
`uv` downloads that interpreter itself, independent of whatever is on `PATH`.

Installing uv, if it isn't already present:

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

It installs to `~\.local\bin` and adds it to your user `PATH` — open a new
terminal afterwards.

## Setup

**1. Start the dependencies.**

```bash
docker compose up -d
```

Postgres on 5432, Redis on 6379. Both have healthchecks; `docker compose ps`
should show `healthy` for each before you continue.

**2. Backend.**

```bash
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

`uv sync` creates `.venv` with Python 3.12, downloading the interpreter if
needed. The defaults in `.env.example` point at the local Docker containers and
work unedited — the Supabase values are there commented out, for when you need
them.

**3. Worker**, in a second terminal.

```bash
cd backend
uv run arq app.workers.settings.WorkerSettings
```

This is what processes uploads. Without it `POST /documents` still answers 202
and the document sits at `pending` forever — which is the intended behaviour, not
a bug: Postgres records that the work is owed, and starting a worker later picks
it up.

It is deliberately not a Compose service. The worker imports the same code the
API does, so running it from the same checkout is what keeps the two honest;
baking it into an image would mean rebuilding to test a task.

**4. Frontend**, in a third terminal.

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

**5. Open http://localhost:3000.** You should see `status ok`, `db ok`,
`redis ok`.

## Verifying it works

```bash
curl http://localhost:8000/health        # 200 {"status":"ok","db":"ok","redis":"ok"}
curl -i http://localhost:8000/health/ready   # 200 {"status":"ready",...}
```

There are two health routes because they answer different questions, and one
route cannot answer both:

- **`/health` is always 200.** The Next page consumes it, so a dead dependency
  has to arrive as readable data rather than as a failed fetch.
- **`/health/ready` returns 503 when any dependency is down.** This is Render's
  healthcheck target. If always-200 were the only contract, a dead Postgres
  would read as a healthy deploy and traffic would keep being routed to it.

To see the difference, stop Postgres and hit both:

```bash
docker compose stop postgres
curl -i http://localhost:8000/health       # still 200, but {"status":"degraded","db":"down"}
curl -i http://localhost:8000/health/ready # 503 {"status":"not_ready",...}
docker compose start postgres              # both recover without restarting the app
```

Recovery needs no restart because the engine is built with `pool_pre_ping`,
which discards dead connections instead of handing them out.

## Uploading a document

There is no upload UI yet — that is the next slice. Two routes, and curl is
enough. You need a course first:

```bash
cd backend
uv run python -m app.cli create-course "Algorithms" \
  --starts-on 2024-09-01 --ends-on 2024-12-15
```

```bash
curl -X POST http://localhost:8000/documents \
  -F "course_id=<the id printed above>" \
  -F "file=@../test-data/Depth-First_Search_Lecture.pdf"
# 202 {"document_id":"...","job_id":"...","reused_document":false}

curl http://localhost:8000/documents/<document_id>/status
# {"status":"ready","chunks_total":9,"chunks_embedded":9,"attempts":1,
#  "run_status":"succeeded","error":null,"stale":false}
```

**202, not 201** — the document exists but is not yet what you asked for. Poll
the status route until `status` is `ready` or `failed`; both are terminal, and a
failed document is still a 200 with the reason in `error`.

Form fields on the upload: `kind` (default `lecture`), `title` (defaults to the
filename), `force` (default false — a re-upload of a document that already has
chunks is a 409 without it, matching the CLI's `--force`), and `embed` (default
true). Pass `embed=false` to skip the OpenAI call when you are testing the same
PDF repeatedly; the document then stops at `processing`, because a document with
no vectors cannot be searched and so is not `ready`.

## Supabase

Production Postgres is Supabase; local is the Docker container. **Both version
numbers in `docker-compose.yml` are pinned to match what Supabase provisions,
and both are bumped by hand when it upgrades.** The floating `pgvector:pg17` tag
put local on pgvector 0.8.6 against Supabase's 0.8.2, and 0.8.x changes index
and operator behaviour — local would quietly accept what production rejects.
`ROADMAP.md` carries the full reasoning.

### What was verified against the real database, and what it settled

`ROADMAP.md` flagged a specific trap: Supabase may install pgvector into the
`extensions` schema rather than `public`, so `CREATE EXTENSION IF NOT EXISTS
vector` can pass locally and still leave the `vector` type unresolvable in
production, depending on `search_path`. Finding that with one trivial migration
is the entire reason this phase exists.

`0001_enable_vector` was run against the Supabase database and probed.
**The trap does not apply here:**

| | |
|---|---|
| Supabase Postgres | 17.6 |
| pgvector | 0.8.2, installed into **`public`** |
| Role default `search_path` | `"$user", public, extensions` |

All three unqualified uses work: the cast `'[1,2,3]'::vector`, a `vector(3)`
column in DDL, and the `<->` operator. **No schema-qualifying and no
`SET search_path` is needed anywhere**, so from Phase 1 onward the models can
name `vector` bare. If that ever changes, it changes here first.

### Document storage

Uploaded PDFs live behind `app/core/storage.py`, and `documents.storage_key`
addresses them as `{user_id}/{filename}` — a key, not a filesystem path. There
are two real backends, selected by `STORAGE_BACKEND`:

| | |
|---|---|
| `local` (default) | files under `backend/.storage`, gitignored |
| `supabase` | a private Supabase Storage bucket, over its REST API |

**`local` is the default so this repo runs on a fresh clone with no credentials
at all** — including `ingest` and `verify`, which is the bar Phase 0 set. Nothing
about local development needs the Supabase values.

`supabase` is what the deploy uses, and it is not optional there: the upload
endpoint and the worker are separate Render services with no shared disk, so a
local path would work on no machine but yours. To point at it, uncomment the
storage block in `.env.example` and supply `SUPABASE_URL`,
`SUPABASE_SERVICE_KEY` and `STORAGE_BUCKET`.

One thing that will cost you an hour otherwise: **the service key must go in the
`apikey` header as well as `Authorization`.** A current `sb_secret_...` key is
not a JWT — the API gateway resolves it from `apikey` — so sending only
`Authorization` fails as `Invalid Compact JWS`, which reads like a corrupted
secret rather than a missing header. `app/core/storage.py` sends both.

### Connection strings

Two variables, `DATABASE_URL` (async, `asyncpg`, for the app) and
`ALEMBIC_DATABASE_URL` (sync, `psycopg`, for migrations). They are separate
rather than one with a driver-prefix string-replace because in production they
may legitimately point at different hosts.

Both use the **session pooler**. The direct connection is IPv6-only without the
IPv4 add-on, and the transaction pooler forbids the prepared statements psycopg
issues automatically. Neither of those is a scaling argument — see the deferred
section of `ROADMAP.md` for the connection cap, which is a real concern later
and not one now.

`backend/.env` is gitignored. Keep the connection string out of shell history
and out of commits.

## Layout

```
backend/
  app/
    api/        routes — no business logic, no raw queries
    core/       config, engine, redis client, the shared health check
  alembic/      migrations
frontend/
  src/app/      App Router; providers.tsx holds the React Query client
docker-compose.yml
```

The backend layering is `api/ → services/ → repositories/`; `services/` and
`repositories/` arrive in Phase 1, when there is something to put in them. The
frontend is organised by feature (`features/upload/`, `features/timeline/`, …),
also from Phase 1. `CLAUDE.md` has the rules for both.
