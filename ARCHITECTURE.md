# Rewind — Architecture

## What it is

Rewind is a **longitudinal learning archive**. A student uploads course material
(lectures, assignments, notes, syllabi), asks a natural-language question —
*"where did I first learn recursion?"* — and gets a **chronological timeline** of
every place that concept appeared, with the first occurrence marked and every hit
linking to the exact page and passage in the source document.

The distinguishing feature is not search. It is **time**. Rewind answers *when*
you learned something and *in what order*, which means the system must know when
each document belongs on the calendar and must be able to sort concept
occurrences cheaply.

---

## Stack

| Layer | Choice |
| --- | --- |
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui |
| Frontend state | React Query (server state), Zustand (client state) |
| Frontend viz | React Flow (Phase 6), Framer Motion (Phase 7) |
| Backend | FastAPI, Pydantic, SQLAlchemy, Alembic |
| Database | Postgres 16 + pgvector |
| Jobs | arq + Redis |
| Storage / DB / Auth | Supabase |
| Deploy | Vercel (frontend), Railway (backend, worker, Redis) |
| Local dev | Docker Compose |

**arq, not Celery.** The job graph is a handful of linear document-processing
pipelines. arq is async-native (matching FastAPI), configured in one file, and
its entire surface area is readable in an afternoon. Celery's broker
abstractions, result backends, and prefork model buy nothing here and cost
comprehension.

---

## Repository layout

```
rewind/
  backend/
    app/
      api/            # FastAPI routers. HTTP only.
      services/       # Business logic. Orchestration.
      repositories/   # Data access. All SQL lives here.
      workers/        # arq task definitions + worker settings.
      models/         # SQLAlchemy ORM models.
      schemas/        # Pydantic request/response models.
      core/           # config, db session, redis client
      main.py
    alembic/
    pyproject.toml
  frontend/
    src/
      app/            # Next.js routes. Thin.
      features/
        upload/       # components/ hooks/ api/
        timeline/
        graph/
        concepts/
      components/ui/  # shadcn primitives. Shared, dumb.
      lib/            # api client, query client, utils
  docker-compose.yml
  ARCHITECTURE.md
  ROADMAP.md
  CLAUDE.md
```

---

## Backend layering

Strict, one direction:

```
api/  →  services/  →  repositories/  →  database
```

- **`api/`** parses and validates input (Pydantic), calls exactly one service,
  serialises the result. **No business logic. No raw queries. No ORM session
  usage beyond passing the dependency through.**
- **`services/`** holds the logic: what "process a document" means, how a date is
  inferred, how a concept is canonicalised. Services may call multiple
  repositories and other services. Services never touch `Request`/`Response`.
- **`repositories/`** is the only place that constructs queries. One repository
  per aggregate (`documents`, `chunks`, `concepts`, …). Returns ORM objects or
  plain tuples — never Pydantic response models.
- **`workers/`** is a second entrypoint, peer to `api/`. Tasks call the *same
  services* as routes. A worker task body should be a handful of lines.

The point is that the interesting logic is reachable from both HTTP and the
queue, and testable without either.

## Frontend structure

Feature-based, not type-based. Each feature owns its vertical:

```
features/timeline/
  components/    # TimelineView, TimelineEntry, FirstOccurrenceBadge
  hooks/         # useConceptTimeline
  api/           # timeline request functions + response types
```

- A feature may import from `components/ui/` and `lib/`.
- A feature should not import from another feature. If two features need the
  same thing, it moves to `components/ui/` or `lib/`.
- `app/` routes compose features. Route files stay thin.

---

## Data model

Ten tables. Postgres 16, `vector` extension enabled in the first migration.

### `users`
`id`, `email`, `created_at`

Exists from migration 1 with a single hardcoded row. Real auth lands in Phase 7.

### `courses`
`id`, `user_id`, `name`, `code`, `term`, `starts_on`, `ends_on`, `created_at`

`starts_on` / `ends_on` are **semester bounds, and they are load-bearing**. They
are the search space for date inference: a lecture whose filename says
"Lecture 07" with no year gets placed by interpolating within the term, and any
parsed or inferred date outside the bounds is rejected as a parse failure rather
than trusted. Without them, dating degrades from "inference" to "guessing."

### `documents`
`id`, `user_id`, `course_id`, `kind`, `title`, `storage_path`,
`occurred_at`, `occurred_at_source`, `status`, `page_count`, `created_at`

- `kind` — `lecture | assignment | note | syllabus`
- `occurred_at_source` — `parsed_syllabus | inferred_filename | manual`
- `status` — ingestion lifecycle (`pending | processing | ready | failed`)

Carries `UNIQUE (id, user_id)` so children can reference the pair — see
invariant 2.

`occurred_at_source` exists so the UI can be honest about confidence. A date from
a parsed syllabus is a fact; a date interpolated from a filename is a guess and
must be visibly marked as one and overridable by the user. Storing *how* we know
a date is as important as the date.

### `chunks`
`id`, `user_id`, `document_id`, `content`, `embedding vector(1536)`,
`page_number`, `chunk_index`, `char_start`, `char_end`, `created_at`

`page_number`, `char_start`, `char_end` are **mandatory, never null**. They are
what turns a retrieval hit into "page 14, this exact paragraph, highlighted."
They are also the one thing in the schema that **cannot be backfilled** — the
offsets only exist during extraction, so a chunk written without them requires
reprocessing the source PDF to recover. Getting these right in Phase 1 is
cheaper than any later fix.

Carries `UNIQUE (id, user_id)`, referenced by `concept_mentions`, and itself
references `documents (id, user_id)` — see invariant 2.

`vector(1536)` matches the embedding model chosen in Phase 1. Changing the model
means a migration plus a full re-embed; treat the dimension as a commitment.

### `concepts`
`id`, `user_id`, `canonical_name`, `slug`, `description`, `embedding`,
`created_at`

Concepts are **per-user**, not global. "Induction" in a discrete math course and
in a philosophy course are different things to different students, and a shared
concept space would require a taxonomy Rewind has no way to earn.

Carries `UNIQUE (id, user_id)` so children can reference the pair — see
invariant 2.

### `concept_aliases`
`id`, `user_id`, `concept_id`, `alias`, `created_at`

Surface forms that map to one concept: `recursion`, `recursive`,
`recursive call`, `self-reference`. Canonicalisation (Phase 5) works by
embedding-similarity clustering; this table is where the result is *persisted*
so it is inspectable and correctable, instead of being recomputed per query.

### `concept_mentions`
`id`, `user_id`, `concept_id`, `chunk_id`, `document_id`, `occurred_at`,
`created_at`

**This is the table the product runs on.** One row per (concept, chunk) hit.

> **`occurred_at` is deliberately denormalized from `documents`.**
> The headline query — the entire demo — is:
>
> ```sql
> SELECT chunk_id, document_id, occurred_at
> FROM concept_mentions
> WHERE concept_id = :concept_id AND user_id = :user_id
> ORDER BY occurred_at ASC;
> ```
>
> With `occurred_at` local and a composite index on
> `(concept_id, occurred_at)`, that is **one index scan, already sorted** — no
> join to `documents`, no sort node, and "first occurrence" is `LIMIT 1`.
> Normalized, every timeline render becomes a join plus a sort over every
> mention of a concept, on the hottest path in the app.
>
> **The cost:** two rows of truth for one fact. Therefore —
> `documents.occurred_at` and `concept_mentions.occurred_at` must only ever be
> written **in the same transaction**. When Phase 3 lets a user manually
> override a document's date, that update *must* cascade to every mention of
> that document. This rule goes in a comment on the column in the migration and
> in the repository method that performs the update. It is the price of the
> index scan and it is paid on purpose.

### `concept_edges`
`id`, `user_id`, `concept_a_id`, `concept_b_id`, `co_occurrence_count`,
`weight`, `created_at`

Undirected co-occurrence graph, built in Phase 6. Stored with
`concept_a_id < concept_b_id` and a unique constraint on the pair so each edge
exists once. `co_occurrence_count` is the raw evidence; `weight` is the derived,
normalised value the graph renders — keeping both means the weighting formula can
change without recounting.

### `learning_events`
`id`, `user_id`, `kind`, `subject_type`, `subject_id`, `occurred_at`, `created_at`

**Append-only.** Never updated, never deleted. A polymorphic log of what the
student did and when (`uploaded_document`, `first_encountered_concept`,
`reviewed_timeline`). This is the substrate for anything retrospective — "your
semester in review", streaks, learning velocity — and none of it can be
reconstructed after the fact if the log was not being written from the start.

### `processing_runs`
`id`, `user_id`, `document_id`, `status`, `attempts`, `error`,
`started_at`, `finished_at`, `created_at`

One row per ingestion attempt. Separate from `documents.status` on purpose:
`documents` holds *current* state, `processing_runs` holds *history*. When a PDF
fails to parse at 2am, this table is the only thing that can say why, how many
times, and whether a retry fixed it.

---

## The invariants

These are not style preferences. Each one is something that is cheap now and
expensive-to-impossible later.

### 1. `created_at` on every table. `occurred_at` on every time-bearing entity. Never conflated.

- `created_at` — when the row was inserted. Bookkeeping. Always
  `TIMESTAMPTZ NOT NULL DEFAULT now()`.
- `occurred_at` — when the *learning actually happened*. Domain data.

A lecture from September uploaded in December has
`occurred_at = September, created_at = December`. Every timeline, every
"first learned", every ordering in the product reads `occurred_at`. A single
place that sorts by `created_at` silently turns Rewind into
"documents in upload order", which is a product that already exists and is not
interesting. Because both columns are timestamps, this bug does not crash — it
just quietly produces wrong answers. Hence: never conflated, and never
`timestamp`-without-timezone.

### 2. `user_id` on every user-scoped table, from the first migration.

Auth arrives in Phase 7. Until then there is one hardcoded user. The column goes
in now anyway, on every table below `users`, including tables whose ownership is
technically derivable through a join (`chunks`, `concept_mentions`,
`concept_aliases`, `concept_edges`, `processing_runs`).

Two reasons. First, retrofitting a tenancy column across nine tables while data
exists is a migration with a backfill and a window where queries are wrong.
Second, Supabase row-level security policies are written per table — a policy
that reads a local `user_id` is one line, while a policy that has to join up to
`documents` to find the owner is slow and easy to get wrong. Redundant here is
correct.

**The redundancy is enforced by the schema, not by convention.** A denormalized
`user_id` that is free to drift is worse than no `user_id` at all: it diverges
silently, and every RLS policy built on it is then wrong in a way nothing
detects. So the parents expose the pair and the children reference it.

`documents`, `concepts`, and `chunks` each carry a `UNIQUE (id, user_id)`
constraint. Redundant on its own — `id` is already unique — but it is what makes
`(id, user_id)` a referenceable key. Every child then uses a **composite foreign
key** that includes `user_id`:

| Child | Composite FK | Parent |
| --- | --- | --- |
| `chunks` | `(document_id, user_id)` | `documents (id, user_id)` |
| `processing_runs` | `(document_id, user_id)` | `documents (id, user_id)` |
| `concept_aliases` | `(concept_id, user_id)` | `concepts (id, user_id)` |
| `concept_edges` | `(concept_a_id, user_id)` | `concepts (id, user_id)` |
| `concept_edges` | `(concept_b_id, user_id)` | `concepts (id, user_id)` |
| `concept_mentions` | `(chunk_id, user_id)` | `chunks (id, user_id)` |
| `concept_mentions` | `(concept_id, user_id)` | `concepts (id, user_id)` |

The effect: a chunk cannot be attached to another user's document, and a mention
cannot bind a concept and a chunk that belong to different users. Postgres
rejects the row on write. Cross-tenant corruption stops being discouraged and
becomes unrepresentable.

#### The asymmetry: why `occurred_at` gets no such guard

`user_id` is an **identity**. It is assigned once and never changes, so a
foreign key can pin it — the database re-checks it on every write, forever, at
no ongoing cost in discipline.

`concept_mentions.occurred_at` is a **mutable value**. Phase 3 lets a user
re-date a document, which must move every mention of it. No constraint can
express *"this copy still equals that copy after both change"* — a composite FK
pins an identity, not a value that is expected to be updated. There is no
structural guard available here.

That is precisely why the same-transaction write rule for `occurred_at` is
genuinely load-bearing rather than one convention among several. Everything else
in this section has been handed to the database; this one thing cannot be, so it
lives in a single repository method, with a comment saying why, and the Phase 3
done-criteria include a consistency query that proves it held.

### 3. Chunks always record `page_number`, `char_start`, `char_end`.

See `chunks` above. Source highlighting depends on it and it cannot be
backfilled without reprocessing.

### 4. Layering is enforced, not aspirational.

Routes contain no business logic and no raw queries. If a route body is doing
anything other than validate → call one service → return, it is wrong.

### 5. Features are vertical.

`features/<name>/{components,hooks,api}`. Shared shadcn primitives in
`components/ui/`. Features do not reach into each other.

---

## Non-goals

- Not a chatbot. The output is a timeline, not prose.
- No multi-user or shared courses. Concepts are per-user.
- No cross-user concept taxonomy.
- No mobile app.
- No abstraction with a single implementation. No interface written "for later."
