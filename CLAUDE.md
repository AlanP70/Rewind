# Rewind — standing context

Longitudinal learning archive. A student uploads course material, asks
*"where did I first learn recursion?"*, and gets a chronological timeline of
every place that concept appeared — first occurrence marked, each hit linking to
the exact page and passage in the source.

Full detail: `ARCHITECTURE.md`. Phase plan: `ROADMAP.md`.

## Stack (decided — do not re-open)

- **Frontend:** Next.js App Router, TypeScript, Tailwind, shadcn/ui,
  React Query (server state), Zustand (client state), React Flow (Phase 6),
  Framer Motion (Phase 7)
- **Backend:** FastAPI, SQLAlchemy, Alembic, Postgres 17 + pgvector
- **Jobs:** arq + Redis — **not Celery**
- **Storage/DB/Auth:** Supabase
- **Deploy:** Vercel (frontend), Render (backend + worker + Key Value, which is
  Render's name for its Redis-compatible service — the app still speaks Redis)
- **Local dev:** Docker Compose

## Invariants (non-negotiable)

1. **Every table has `created_at`. Time-bearing entities also have
   `occurred_at`. Never conflate them.** `created_at` = row insertion.
   `occurred_at` = when the learning actually happened. All ordering, all
   timelines, all "first learned" logic reads `occurred_at`. Both are
   `TIMESTAMPTZ`.
2. **`user_id` on every user-scoped table from the first migration** — including
   tables where ownership is derivable by join. Auth is Phase 7; until then
   there is one hardcoded user. The column still goes in now.
3. **Chunks always record `page_number`, `char_start`, `char_end`.** Never null.
   Source highlighting depends on them and they cannot be backfilled without
   reprocessing the PDF.
4. **`concept_mentions.occurred_at` is deliberately denormalized** from
   `documents` so the headline timeline query is one index scan on
   `(concept_id, occurred_at)`. Consequence: `documents.occurred_at` and the
   matching `concept_mentions.occurred_at` rows are only ever written **in the
   same transaction**. No constraint can enforce this, so it is funnelled:
   exactly one service function, `redate_document`, writes
   `documents.occurred_at`. Nothing else touches that column.
5. **Backend layering:** `api/` → `services/` → `repositories/`, plus
   `workers/`, `models/`, `schemas/`. Routes contain **no business logic and no
   raw queries**. All SQL lives in `repositories/`. Worker tasks call the same
   services as routes.
6. **Frontend is feature-based:** `features/upload/`, `features/timeline/`,
   `features/graph/`, `features/concepts/` — each with its own `components/`,
   `hooks/`, `api/`. Shared shadcn primitives live in `components/ui/`. Features
   do not import from each other.

## Schema (10 tables)

`users`, `courses`, `documents`, `chunks`, `concepts`, `concept_aliases`,
`concept_mentions`, `concept_edges`, `learning_events`, `processing_runs`.

Rationale for each column choice is in `ARCHITECTURE.md` — read it before
changing the schema.

## How we work

- **Vertical slices only.** Every change ends with something that runs.
- **State the plan before writing code:** goal, files to create, files to
  modify, approach. Then wait for go-ahead.
- **Never dump hundreds of lines at once.**
- **Ask when a decision is genuinely ambiguous.** Do not guess and move on.
- **No speculative abstraction.** No interface with one implementation. No code
  written "for later."
- **Do not build past the current phase.**
- Alan must be able to explain every file in this repo. If an explanation would
  take more than a couple of sentences, the code is wrong.

## Current phase

**Phase 2 — job queue.** Scope: migration for `processing_runs`; an arq worker
whose task calls the *same service* as the CLI; `POST /documents` that enqueues
and returns a document id immediately; `GET /documents/{id}/status` reporting
status and progress; `features/upload/` with drag-drop and a polling progress UI;
retry with attempt counting and a recorded `error` on failure. Nothing else — no
search, no dating, no concepts.

**Read ROADMAP.md's Phase 2 section before writing any of it.** The "done when"
bar is behavioural, not structural: a corrupt PDF must end `failed` with a
*readable* error in `processing_runs` and the API must say so, two documents
uploaded at once must both complete, and the worker must survive a restart
mid-job without losing the document.

Carried in from an earlier phase: Render's free Key Value plan has **no
persistence** (see ROADMAP's deferred section), so Redis cannot be the record of
truth for outstanding work — Postgres is, and Redis is only the dispatch
mechanism.

Slices 1, 2 and 3 are done; only slice 4 (`features/upload/`) remains. Slice 3
landed the arq worker, `POST /documents` and `GET /documents/{id}/status`. Three
of the four done-when criteria are met and verified live — corrupt PDF, two
concurrent uploads, worker killed mid-job — and the fourth is met on the HTTP
side, awaiting the UI. Two findings from it are load-bearing and written up in
ROADMAP's "Settled in slice 3": **arq retries only on `Retry`, `RetryJob` and
`CancelledError`**, so permanent is the path that returns and transient is the
path that raises `Retry`; and **`stale_run_after_seconds` must exceed
`job_timeout + 10`**, because that is arq's in-progress lock and a shorter
threshold reports `stale` on a job arq is about to re-claim.

Slice 2 landed storage: `documents.storage_key` (renamed
from `storage_path` by migration `0004`) is a key of the form
`{user_id}/{filename}`, resolved through `app/core/storage.py`, which has two
real backends selected by `STORAGE_BACKEND` — `local` under `backend/.storage`
for a credential-free clone, `supabase` for the deploy. Ingestion takes bytes and
`verify` downloads; nothing on that path reads local disk. Uploading is the
caller's job, not `process_document`'s, because slice 3's worker can only ever be
handed a key. Full reasoning in ROADMAP's "Settled in slice 2".

Phase 1 is closed, tagged `phase-1-complete`. A real lecture PDF ingests end to
end, all three offset-verification layers pass, `verify` was proved able to fail
by fault injection, re-ingest is idempotent, and embedding is resumable with
per-batch commits. Documents currently reach `ready` only when every chunk has a
vector.

Phase 0 is closed. Its deploy is live end to end (Vercel → Render → Supabase +
Redis, CORS scoped to the Vercel domain), and both remaining checks were run on
2026-08-01: the degraded path (`/health` → 200 `{"status":"degraded","db":"down"}`,
`/health/ready` → 503, both recovering with no app restart) and a fresh clone
reaching a running local app using only the README.
