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

**Phase 3 — dating.** Scope: syllabus parsing, filename inference, a manual
override endpoint, and a UI showing each document's date and where that date came
from. Documents that cannot be dated are surfaced, never silently defaulted.
Nothing else — no search, no concepts.

**Read ROADMAP.md's Phase 3 section before writing any of it.** The load-bearing
constraint is invariant 4 made concrete: **exactly one service function,
`redate_document`, writes `documents.occurred_at`**, and all three dating paths
route through it. It exists before there is anything to cascade to, because
Phase 5 adds a write that must accompany every date change and no constraint can
force it.

Two items are explicitly carried into this phase by ROADMAP: `documents` has no
`ON DELETE CASCADE` — **re-checked in slice 1 and corrected: Phase 3 is not where
that stops being free**, nothing in this phase deletes a document, and the
trigger is now the condition "first code path that deletes a `documents` row";
and Phase 2's frontend has no test runner, so if Phase 3's dating UI justifies
one, the `describe()` assertions in Phase 2's outstanding-gaps item land in the
same slice.

Slice 1 landed the funnel, migration `0005` (which adds `filename_date`, so the
enum is four values, not ROADMAP's original three), and `PATCH
/documents/{id}/date`. The sole-writer rule is enforced by an **AST test**, not
convention — `tests/test_occurred_at_sole_writer.py` — with two further tests
proving that guard can fail and that it ignores prose. Its first run failed on
`redate_document`'s own docstring, and the generalised lesson is in ROADMAP: **a
guard is an incentive, and the failure mode to watch is a check whose cheapest
fix damages what the check protects.**

Slice 2 landed `services/filename_dates.py` — three pure functions over a string
plus the term, pure so the heuristic can be measured without a database — plus
`date_course_from_filenames` and the `date-course` CLI command. **Measured on 70
real MIT 6.006 filenames from OCW (committed as a labelled TSV): 58 correct, 12
undated, 0 wrong.** That measures ordinal *extraction* against decoy numbers, not
date accuracy; those filenames carry no dates, so `inferred_filename`'s date
accuracy is **unmeasured and unclaimed**. Interpolation is expected to drift —
real timetables are unevenly spaced, and the range depends on which files the
student happened to upload — but it is **monotonic, so it never reorders**. Order
is reliable, the date is not; that asymmetry is the whole point of
`occurred_at_source`. Every ambiguous branch resolves to undated, and a hand-set
date is never overwritten, including under `--overwrite`.

**Two Phase 2 gaps were fixed after it closed, on 2026-08-09** — see ROADMAP's
"Corrected after the phase closed". The worker had no Render service, so the
deployed app accepted uploads and ran nothing. `render.yaml` now declares the
deploy, as **one free service running both uvicorn and arq** — a separate Render
worker needs a paid plan. `start.sh` supervises them: it does not `exec`, because
Docker signals PID 1 only and arq would never get SIGTERM, and it exits when
either child exits, because a live API over a dead worker is a green dashboard
above a queue that silently stopped draining. And `submit_document` never wrote
the `queued` `processing_runs` row
that this phase's first constraint says it writes, so a dropped job left no trace
at all; it does now, and `process_document` claims that row rather than opening a
second one. **Both were recorded here and in ROADMAP as settled fact before they
were built.** Treat a prose claim about behaviour as a hypothesis until the code
says the same thing — this was the second occurrence, after arq's retry
semantics.

**Both were then observed in production on 2026-08-10**, not just locally — see
ROADMAP's "Verified in production". An upload through the Vercel page reached
`Ready — 9 chunks`, closing gap 1 where it actually failed; and a POST/GET race
caught `run_status: "queued"`, `attempts: 1` before arq claimed it, with the same
document later reading `succeeded`, `attempts: 1` — one row, not two. **The
window is arq's `poll_delay` (0.5s), not the free instance spinning down**:
`start.sh` starts arq before uvicorn, so nothing worker-less is reachable over
HTTP. That work also surfaced a Deferred item: a fresh deploy has no courses and
no way to create one except the CLI against its own database, so it is unusable
through its own UI until someone with credentials intervenes.

Phase 2 is closed, tagged `phase-2-complete`. The queue works end to end: a
corrupt PDF fails readably, two concurrent uploads both finish, and a worker
killed mid-job re-claims its document. Redis is only dispatch — Postgres is the
record of intent, because Render's free Key Value plan has no persistence.

Three rules from Phase 2 that the code depends on and cannot enforce:
**`Progress` is deliberately absent from `components/ui/`** — installing it is how
a fabricated extraction percentage gets added later; **`stale` renders as an
addition to the stage, never a replacement**, so a stranded job still says which
phase stranded it; and **`stale_run_after_seconds` must exceed `job_timeout + 10`**,
which is arq's in-progress lock.

Slice 3 landed the arq worker, `POST /documents` and
`GET /documents/{id}/status`. Two findings from it are load-bearing and written
up in ROADMAP's "Settled in slice 3": **arq retries only on `Retry`, `RetryJob`
and `CancelledError`**, so permanent is the path that returns and transient is
the path that raises `Retry`; and **`stale_run_after_seconds` must exceed
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
