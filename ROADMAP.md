# Rewind — Roadmap

Eight phases. Each is a **vertical slice**: it ends with something that runs and
can be demonstrated, not a layer waiting for the layer above it.

Rules that apply to every phase:

- Do not build past the current phase. Not "while I'm in here."
- A phase is done when its done-criteria are literally true, checked by running
  something — not when the code exists.
- Every phase ships to Render/Vercel. `main` is always deployable.

---

## Phase 0 — Deploy an empty app end to end

**Goal:** Prove the whole pipe — browser → Vercel → Render → Postgres/Redis —
before there is any product in it. All deployment pain is paid once, now, when
there is nothing to debug except the deployment.

**Deliverable**
- `docker-compose.yml`: Postgres with pgvector (`pgvector/pgvector:pgNN`), Redis.
- FastAPI app with **two** health routes over one shared check function:
  - `GET /health` — **always 200**, body `{status, db, redis}`. This is what the
    Next page consumes.
  - `GET /health/ready` — **503 if any dependency is down**. This is Render's
    healthcheck target.
- `CORSMiddleware`, allowed origins **from an env var**.
- Alembic initialised; one migration, `0001_enable_vector`, whose entire job is
  `CREATE EXTENSION IF NOT EXISTS vector`.
- Next.js app with one page that fetches `/health` and displays the result.
- `.env.example` and a `README.md` with setup steps.

**Nothing else. No models, no routes beyond health, no UI beyond that page.**

**Constraints settled before writing any of it**

- **Python 3.12**, pinned in `pyproject.toml`, interpreter provisioned by `uv`.
  System Python on this machine is **3.14.4 and is not to be used** — wheel
  availability on Windows is not guaranteed that far ahead, and this machine has
  a history of PATH conflicts between interpreters.
- **The Postgres major version in `docker-compose.yml` must match whatever the
  Supabase project provisions.** Ask for the number before writing the file;
  do not default to 16. Local and production disagreeing on the major version
  turns Phase 0 into a debugging exercise about the wrong thing.

  **The pgvector version is pinned for the same reason.** The image tag is
  `pgvector/pgvector:0.8.2-pg17`, not `pg17`, because the floating tag ran local
  ahead of Supabase (0.8.6 vs 0.8.2) and 0.8.x changes index and operator
  behaviour — local would quietly accept what production rejects, and Phase 4
  adds HNSW indexes. **Both numbers are bumped by hand when Supabase upgrades.**
  That maintenance cost is accepted: catching a version gap here, with one
  migration and no data, is cheaper than catching it in Phase 4 with real data.
- **React Query: provider plus one inline `useQuery`.** No query-key factory, no
  `useHealth` wrapper, no `lib/queries/` directory. One page does not justify the
  scaffolding, and the scaffolding is easier to add when there is a second caller
  than to unpick when there isn't.
- Backend deps: `asyncpg` for the app, sync `psycopg` for Alembic.
- Compose runs Postgres and Redis only; backend and frontend run on the host.
  `backend/Dockerfile` exists for Render, not for local dev. Its `CMD` is
  `backend/start.sh`, which migrates and then `exec`s uvicorn — Render's Docker
  Command field cannot be trusted to parse a compound command, and its free tier
  has no pre-deploy step. **Leave that field blank** so the Dockerfile wins.
- **The Key Value service and the web service must be in the same Render
  region.** The internal URL (`redis://red-xxxxxxxx:6379`, no password) resolves
  only within one region: a Key Value in Ohio and a web service in Oregon fail
  with `gaierror: Name or service not known` on a hostname that is otherwise
  perfectly correct, so it reads as a bad `REDIS_URL` rather than as a topology
  mistake. The tell is `gaierror` rather than a refused connection or a timeout —
  the name does not exist in that resolver's view at all. Region is fixed at
  creation, so the fix is to recreate the instance in the web service's region
  and update `REDIS_URL`. The cross-region alternative is the external URL, which
  is `rediss://` with a password over the public internet — TLS and egress on
  every call, for a connection that should never leave Render's network.

**Done when**
- `docker compose up` gives working Postgres and Redis.
- `alembic upgrade head` succeeds from empty; `SELECT * FROM pg_extension` lists
  `vector`.
- **`0001_enable_vector` is verified against the real Supabase database, not only
  local Postgres.** Supabase may install pgvector into the `extensions` schema
  rather than `public`, so `CREATE EXTENSION IF NOT EXISTS vector` can pass
  locally and still leave the `vector` type unresolvable in production depending
  on `search_path`. Finding that with one trivial migration is the entire reason
  this phase exists; finding it with ten is a bad afternoon.
- `GET /health` returns `{status: "ok", db: "ok", redis: "ok"}` locally, and still
  returns 200 with `db: "down"` when Postgres is stopped.
- `GET /health/ready` returns 503 when Postgres is stopped, 200 when it is up.
  **Reason this route exists:** always-200 as the only contract means a dead
  Postgres reads as a healthy deploy, and Render keeps routing traffic to it.
- CORS is proven by the deployed Vercel page rendering health from the deployed
  Render backend — a real cross-origin browser fetch, not curl. Origins come
  from the env var; a localhost placeholder holds until the first deploy supplies
  the real value. **On Render's free tier, distinguish a CORS failure from a cold
  start**: an idle instance spins down, so the first request after inactivity can
  fail or hang for ~30–60s. A reload that succeeds means the deploy was asleep,
  not that CORS is misconfigured.
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
- **Exactly one service function — `redate_document` — writes
  `documents.occurred_at`.** All three override paths (syllabus parse, filename
  inference, manual correction) route through it; no other code touches that
  column. Its docstring records that Phase 5 extends it to cascade to
  `concept_mentions` in the same transaction.

  The funnel is the point. Phase 5 adds a write that must accompany every date
  change, and there is no constraint that can force it (see the asymmetry note in
  `ARCHITECTURE.md`). One function is the only place that obligation can be
  reliably attached, so it exists before there is anything to cascade to.

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
- **`redate_document` now cascades**: re-dating a document updates every one of
  its `concept_mentions` rows in the same transaction. Proven by a consistency
  query returning zero rows, run after a manual re-date:

  ```sql
  SELECT cm.id
  FROM concept_mentions cm
  JOIN documents d ON d.id = cm.document_id
  WHERE cm.occurred_at <> d.occurred_at;
  ```

  The failure mode this catches: a user corrects a lecture's date, the document
  row updates, and the timeline silently does not move. Nothing errors; the answer
  is just wrong.
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

---

## Deferred — operational concerns with no phase yet

- **Revisit the Supabase session pooler's connection cap before this has real
  concurrent users.** Two options: the direct connection with the IPv4 add-on, or
  transaction-mode PgBouncer with prepared statements disabled app-side
  (`statement_cache_size=0` for asyncpg). Phase 0 chose the session pooler to
  avoid IPv6-only direct connections and psycopg's auto-prepare, neither of which
  is a scaling argument.

- **Render's free Key Value plan has no persistence — settle this before Phase 3
  puts arq on it.** A restart or eviction drops everything in it. For Phase 0 that
  is irrelevant, because the only thing touching Redis is a health ping with no
  state to lose. Once arq holds a job queue there, a restart mid-document silently
  loses enqueued work, and the honest fix is a paid plan rather than
  application-level retry logic. Whatever instance ends up serving arq, it has to
  sit in the web service's region — see Phase 0's constraints for why, and for
  what the failure looks like when it doesn't.

- **Render's free web service spins down when idle, so `/health/ready` is not an
  uptime check there.** Render's own healthcheck does not keep a free instance
  awake; an external pinger would, at the cost of the monthly instance-hour
  allowance. Treat the route as a deploy gate and a dependency probe — which is
  what it was built for — not as monitoring.
