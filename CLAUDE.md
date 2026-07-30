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
- **Backend:** FastAPI, SQLAlchemy, Alembic, Postgres 16 + pgvector
- **Jobs:** arq + Redis — **not Celery**
- **Storage/DB/Auth:** Supabase
- **Deploy:** Vercel (frontend), Railway (backend + worker + Redis)
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

**Phase 0 — deploy an empty app end to end.** Scope: docker-compose (Postgres 16
+ pgvector, Redis), FastAPI with `GET /health`, Alembic with one migration
enabling `vector`, a Next.js page that displays `/health`, `.env.example`,
README. Nothing else.
