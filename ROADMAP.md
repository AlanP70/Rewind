# Rewind — Roadmap

Eight phases. Each is a **vertical slice**: it ends with something that runs and
can be demonstrated, not a layer waiting for the layer above it.

Rules that apply to every phase:

- Do not build past the current phase. Not "while I'm in here."
- A phase is done when its done-criteria are literally true, checked by running
  something — not when the code exists.
- Every phase ships to Railway/Vercel. `main` is always deployable.

---

## Phase 0 — Deploy an empty app end to end

**Goal:** Prove the whole pipe — browser → Vercel → Railway → Postgres/Redis —
before there is any product in it. All deployment pain is paid once, now, when
there is nothing to debug except the deployment.

**Deliverable**
- `docker-compose.yml`: Postgres 16 with pgvector, Redis.
- FastAPI app with a single route: `GET /health`, reporting DB and Redis
  connectivity.
- Alembic initialised; one migration whose entire job is
  `CREATE EXTENSION IF NOT EXISTS vector`.
- Next.js app with one page that fetches `/health` and displays the result.
- `.env.example` and a `README.md` with setup steps.

**Nothing else. No models, no routes beyond health, no UI beyond that page.**

**Done when**
- `docker compose up` gives working Postgres and Redis.
- `alembic upgrade head` succeeds from empty; `SELECT * FROM pg_extension` lists
  `vector`.
- `GET /health` returns `{db: "ok", redis: "ok"}` locally.
- The deployed Vercel page renders the health status from the deployed Railway
  backend.
- A fresh clone reaches a running local app using only the README.

---

## Phase 1 — Ingestion

**Goal:** Turn a PDF into queryable rows: text → chunks → embeddings, with exact
source positions preserved.

**Deliverable**
- Migration creating `users`, `courses`, `documents`, `chunks`.
- PDF text extraction retaining page boundaries and character offsets.
- Chunking strategy that records `page_number`, `chunk_index`, `char_start`,
  `char_end` for every chunk.
- Embedding generation, batched, into `chunks.embedding vector(1536)`.
- A CLI command: `ingest <course_id> <path>`.

**No UI. No queue.** Ingestion runs synchronously in the CLI.

**Done when**
- A real lecture PDF ingests end to end.
- For a random sample of chunks, `char_start`/`char_end` slice the source page
  text back to exactly the stored `content`. This is verified, not assumed.
- No chunk has a null `page_number`, `char_start`, or `char_end`.
- Re-ingesting the same document does not create duplicates.

---

## Phase 2 — Job queue

**Goal:** Move ingestion off the request path and make its progress and failures
visible.

**Deliverable**
- Migration for `processing_runs`.
- arq worker; ingestion becomes a task calling the *same service* as the CLI.
- `POST /documents` enqueues; returns immediately with a document id.
- `GET /documents/{id}/status` reports current status and progress.
- `features/upload/` — drag-drop upload plus a polling progress UI.
- Retry with attempt counting and a recorded `error` on failure.

**Done when**
- Uploading through the UI produces a document that reaches `ready` without any
  manual step.
- A deliberately corrupt PDF ends `failed` with a readable error in
  `processing_runs`, and the API says so.
- Two documents uploaded at once both complete.
- The worker survives a restart mid-job without losing the document.

---

## Phase 3 — Dating

**Goal:** Every document gets an `occurred_at` and an honest account of where
that date came from.

**Deliverable**
- Syllabus parsing: extract a schedule (week/date → topic) → `parsed_syllabus`.
- Filename inference: `Lecture 07`, `hw3`, `2024-09-14` interpolated against
  `courses.starts_on`/`ends_on` → `inferred_filename`.
- Manual override endpoint → `manual`, which must cascade `occurred_at` to that
  document's `concept_mentions` in the same transaction.
- UI showing each document's date, its source, and a control to correct it.
- Documents that cannot be dated are surfaced for the user to fix, not silently
  defaulted.

**Done when**
- A syllabus-dated course has correct dates for its lectures.
- Any inferred date outside the course's term bounds is rejected rather than
  stored.
- `occurred_at_source` is accurate for every document — spot-checked by hand.
- A manual override updates document and mentions atomically, verified by query.
- **Re-dating a document cascades to every one of its `concept_mentions` rows**,
  proven by a consistency query that returns zero rows:

  ```sql
  SELECT cm.id
  FROM concept_mentions cm
  JOIN documents d ON d.id = cm.document_id
  WHERE cm.occurred_at <> d.occurred_at;
  ```

  The failure mode this catches: a user corrects a lecture's date, the document
  row updates, and the timeline silently does not move. Nothing errors and the
  answer is just wrong. (Phase 3 predates `concept_mentions`, so this check goes
  in as part of Phase 5 and is run against Phase 3's override path from then on.)

---

## Phase 4 — Retrieval + timeline (the demo)

**Goal:** The thing the project is for. Ask a question, get a chronological
timeline with the first occurrence marked and every hit linked to its page.

**Deliverable**
- Vector search over `chunks` with an HNSW index.
- `POST /search` → hits with document, page, passage, and `occurred_at`.
- `features/timeline/` — chronological timeline, first occurrence badged, each
  entry linking to the source page with the passage highlighted.
- **A 15-question eval set** with expected first-occurrence documents, run by a
  script that prints pass/fail per question.

**Done when**
- "Where did I first learn recursion?" returns a correct, chronologically
  ordered timeline over real course material.
- The eval script runs in one command and reports a score.
- Every timeline entry deep-links to the right page, and the highlight lands on
  the right passage.
- Timeline query latency is measured and recorded in the README.

---

## Phase 5 — Concepts + canonicalisation

**Goal:** Stop treating each query as a fresh search. Promote recurring ideas to
first-class `concepts` with alias sets.

**Deliverable**
- Migration for `concepts`, `concept_aliases`, `concept_mentions`.
- Concept extraction from chunks.
- Canonicalisation by embedding similarity: variants above a tuned threshold
  collapse into one concept with rows in `concept_aliases`.
- Backfill of `concept_mentions`, writing `occurred_at` denormalized from
  `documents` — same transaction, per the invariant.
- `features/concepts/` — browse concepts, see aliases, jump to a timeline.

**Done when**
- `recursion` / `recursive` / `recursive call` resolve to one concept.
- Timelines are served from `concept_mentions` by concept id, and the query plan
  is confirmed to be an index scan on `(concept_id, occurred_at)` — checked with
  `EXPLAIN`.
- The similarity threshold is a documented number with the false-merge cases that
  set it written down.
- Phase 4's eval set still passes.

---

## Phase 6 — Knowledge graph

**Goal:** Show how concepts connect, not just when they appeared.

**Deliverable**
- Migration for `concept_edges`.
- Co-occurrence computation from `concept_mentions` sharing a document (and,
  more tightly, a chunk), stored with `concept_a_id < concept_b_id`.
- Weighting derived from `co_occurrence_count`, stored in `weight`.
- `features/graph/` — React Flow rendering with a **capped node count**;
  low-weight edges and long-tail nodes are pruned before render.
- Clicking a node opens that concept's timeline.

**Done when**
- The graph for a real course is readable — not a hairball — at the node cap.
- The cap is enforced server-side; the client never receives an unbounded graph.
- Node click navigates to the correct concept timeline.
- Graph payload size and render time are measured.

---

## Phase 7 — Auth, demo course, polish

**Goal:** Something a stranger can use, and something that shows what it does
without them uploading anything first.

**Deliverable**
- Supabase auth; the hardcoded user disappears. RLS policies on every
  user-scoped table using its local `user_id`.
- A seeded demo course from **MIT OCW 6.006** (CC-licensed), browsable without
  signing up.
- Landing page explaining the idea in one screen, with the timeline as the hero.
- Framer Motion on the timeline and graph transitions.
- Empty states, error states, loading skeletons.

**Done when**
- Two accounts cannot see each other's data — verified by trying, at the API
  level, not just the UI.
- A logged-out visitor can explore the demo course and see a real timeline.
- Every route has a defined empty state and error state.
- A stranger reaches "I understand what this does" from the landing page without
  explanation.
