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
- A CLI, `python -m app.cli`, with `create-course`, `ingest <course_id> <path>`,
  and `verify <document_id>`.

**No UI. No queue.** Ingestion runs synchronously in the CLI.

**Constraints settled before writing any of it**

- **The PDF library is `pdfplumber`. Not pypdf, not PyMuPDF.** Two reasons, both
  load-bearing. It is MIT licensed, and PyMuPDF is AGPL — ruled out for a repo
  that may go public. And its char objects come out of the *same layout pass* as
  the text, so if Phase 4's highlighting ever needs bounding boxes, the
  coordinate data is already reachable without swapping libraries and re-ingesting
  everything. A different library's offsets are a different coordinate system;
  per invariant 3 the offsets cannot be backfilled, so changing this later means
  reprocessing every PDF. The cost is speed, and it lands in a CLI where nobody
  is waiting.

  **`pdfplumber` is pinned with `==`, and bumping it requires a full re-ingest.**
  Same argument as the Postgres and pgvector pins in Phase 0, with higher stakes:
  a version bump that changes extracted text by one character does not error, it
  silently shifts every stored offset, and invariant 3 says those cannot be
  backfilled. The failure surfaces much later as highlights landing on the wrong
  passage. `verify` is what catches it — run it after any bump.
- **Embeddings are OpenAI `text-embedding-3-small` at 1536 dimensions**, matching
  `chunks.embedding vector(1536)`. The dimension is a commitment: changing the
  model means a migration plus a full re-embed. The API key is needed for the
  embed step and nothing before it.
- **Chunks never span pages.** `char_start`/`char_end` are **page-local** indices
  into that one page's extracted text — not offsets into the whole document.
  Every consumer of those columns depends on this reading.
- **The chunker is a pure function from a page string to `(start, end)` index
  pairs. It never returns text.** Content is produced exactly once, by slicing
  that same string with those pairs, so `text[start:end] == content` holds *by
  construction* rather than by agreement between two code paths. This is the
  structural reason the invariant holds; the three verification layers below are
  what prove it did.
- **Chunk size 1000 characters, 150 overlap, split on a ladder of separators:
  blank line, then sentence end, then line break, then space, then a hard cut.**

  **A bare line break has to be one of the tiers.** Measured against the 6.006
  DFS lecture: it contains *zero* blank lines and between zero and five
  sentence-ending periods per page, because slide-style lecture notes are bullet
  lines joined by single newlines. Paragraph-then-sentence alone finds no
  boundary anywhere on that document and falls straight through to cutting
  mid-word.

  **Sentence ranks above line break, and the order matters both ways.** In
  hard-wrapped prose a line break lands mid-sentence and is the worse split; in
  slide notes the sentence tier finds nothing and the line tier takes over. This
  order is the one that behaves for both kinds of document.

  **A boundary is only accepted once the chunk is 70% full** (`MIN_FILL`). At
  50%, one stray period early in the window wins the sentence tier and emits a
  236-character chunk beside 1000-character ones; 0.7 removes the runt and takes
  the document from 10 chunks to 9; 0.75 and above produce a different runt.
  Re-measure if the target size changes.

  **All of the above was tuned against exactly one document** — the 6.006 DFS
  lecture, a slide-style PDF. It is not validated across document types. An
  assignment sheet or a prose-heavy syllabus has different structure and may want
  different numbers, and the sentence-above-line-break ordering in particular has
  only been exercised on material with almost no sentences in it. Revisit this
  deliberately when the second and third document kinds arrive, rather than
  meeting it later as a mystery about why some documents chunk badly.
- **Courses are created by a `create-course` subcommand**, not seeded in a
  migration. `starts_on`/`ends_on` are real data that Phase 3 infers dates
  against, not fixture data.
- **Re-ingesting the same `storage_path` is idempotent**: the same document row,
  with its chunks deleted and rebuilt in one transaction. `--force` is what
  permits the destructive re-chunk; without it the run refuses rather than
  quietly doubling a document's chunks.
- **The CLI is stdlib `argparse`.** Three subcommands do not justify a dependency.

**Offset verification has three layers, not one**

The offsets are the one thing in this phase that cannot be repaired after the
fact, so they are checked at three levels that fail for different reasons:

1. **A property test on the pure chunker**, against synthetic strings. No PDF, no
   database. Catches chunker logic bugs in isolation.
2. **`ingest --dry-run`** against the real PDF, asserting the same property and
   writing nothing. Catches whatever real extracted text does that synthetic
   strings don't.
3. **The `verify` command**, which **re-extracts the PDF in a separate process**
   from `storage_path` — reusing nothing cached in memory — and asserts
   `page_text[char_start:char_end] == content` byte for byte. Running it in a
   fresh process is the whole point: it catches round-trip corruption through
   Postgres *and* non-deterministic extraction, neither of which an in-memory
   check can structurally detect.

**`storage_path` is repo-relative, resolved to absolute at read time**

An absolute path is machine-specific data written into the database: `verify`
re-extracts from `storage_path`, so storing `C:\Users\...` means verification can
only ever pass on one laptop. That is the same class of mistake as hardcoding
`localhost`. Paths are therefore stored relative to the repo root, with POSIX
separators, and resolved against the repo root when read.

The test corpus is committed for the same reason — a repo that cannot run its own
`verify` on a clean clone fails the fresh-clone bar set in Phase 0. The MIT OCW
material is CC-licensed, so redistributing it is fine, and it is the same corpus
Phase 7's demo course uses.

**This column becomes a storage key when Supabase Storage lands in Phase 2.**
Phase 2 replacing it is a reason to change it again later, not a reason to write
something knowingly broken now.

**Known deviations and outstanding gaps**

- **Course term bounds (`ends_on >= starts_on`) are validated in the service, not
  by a CHECK constraint**, which is inconsistent with every comparable rule in the
  schema. Migration `0002` is already applied, and fixing this properly means an
  `0003` for a rule nothing depends on yet. Fold it in if `courses` is touched
  again for another reason; do not spend a migration on it in isolation.
- **The re-ingest guard has no automated test.** That `--force` is required, and
  that a re-ingest replaces rather than duplicates chunks, is currently verified
  by running the CLI by hand. A regression test needs database fixtures; add it
  when fixtures get built for something else, so the setup cost is amortised
  rather than paid for a single test.

**Done when**
- A real lecture PDF ingests end to end.
- For a random sample of chunks, `char_start`/`char_end` slice the source page
  text back to exactly the stored `content`. This is verified, not assumed — by
  all three layers above, `verify` included.
- No chunk has a null `page_number`, `char_start`, or `char_end`.
- Re-ingesting the same document does not create duplicates.

**Status — complete except the embedding slice** (as of 2026-08-05)

Done, verified by running against the 6.006 DFS lecture:

- `users`, `courses`, `documents`, `chunks` migrated; `alembic downgrade base`
  then `upgrade head` confirmed clean from empty.
- Extraction, chunking, `create-course`, `ingest`, `ingest --dry-run`, `verify`.
- All three verification layers pass. `verify` was proved able to *fail*, not
  just to pass, by injecting three faults into the database — a changed character
  in `content`, a `char_start` shifted by one, and a wrong `page_count` — and
  confirming each is caught and reported with the offset of the first divergence.
- Re-ingest refuses without `--force` and replaces rather than duplicates with it:
  three ingests of the same file leave one document and nine chunks.

**Outstanding: batched embeddings into `chunks.embedding`.** Needs an
`OPENAI_API_KEY`, which is deliberately not yet configured. Until that slice
lands there is no `openai` dependency, no key in `config.py` or `.env.example`,
and every chunk's `embedding` is null.

**Ingested documents therefore stop at status `processing`, by design.** A
document with no vectors cannot be searched, so marking it `ready` would make
Phase 4 look broken rather than incomplete. The embedding slice is what advances
it to `ready`; nothing else should.

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
