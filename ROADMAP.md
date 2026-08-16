# Rewind — Roadmap

Eight phases. Each is a **vertical slice**: it ends with something that runs and
can be demonstrated, not a layer waiting for the layer above it.

Rules that apply to every phase:

- Do not build past the current phase. Not "while I'm in here."
- A phase is done when its done-criteria are literally true, checked by running
  something — not when the code exists.
- Every phase ships to Render/Vercel. `main` is always deployable.
- **A claim about a library's behaviour goes in this file only after it has been
  read in that library's source or proved by running it — never from how it
  plausibly ought to work.** Mark anything unverified as an assumption, in those
  words.

  This document is load-bearing. Decisions here get implemented confidently and
  months later, by someone who reasonably treats them as settled and does not
  re-derive them — so a wrong claim is not a note to correct, it is a bug with a
  citation. Phase 2's retry rules asserted that arq retries any exception under
  `max_tries`. It does not, it was never checked, and the code was written to
  match the sentence. See the arq bullet in Phase 2 for what that would have
  cost.

  The failure mode that makes this worth a rule: a wrong claim about a library
  usually produces **no error at all**. `max_tries` silently did nothing, and the
  symptom — transient failures never retrying — is one you would attribute to the
  network long before the queue.

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

  **The key is scoped to `/v1/embeddings` only.** Phase 5's concept extraction
  needs chat completions, and it will fail with a *permissions* error — not a
  quota or billing error, which is what one would go looking for first. Widen the
  key or issue a second one before starting that phase.

- **Embedding commits per batch, and a failed run resumes rather than restarts.**
  The work list is "chunks where `embedding IS NULL`", so nothing is ever billed
  twice. One transaction across all batches was rejected deliberately: a transient
  failure on the last batch of a semester would discard every vector already paid
  for, and there is nothing atomic to protect, because a chunk's embedding depends
  on that chunk alone.

  **A half-filled embedding column is never left under a `processing` status.** On
  failure the document is moved to `failed` in its own transaction and the CLI
  prints the resume command. `ready` is set only when a count confirms no chunk is
  missing a vector — never inferred from the loop finishing.
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
- **Re-ingesting the same `storage_key` is idempotent**: the same document row,
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
   from the bytes `storage_key` addresses — reusing nothing cached in memory,
   and since slice 2 nothing on local disk either — and asserts
   `page_text[char_start:char_end] == content` byte for byte. Running it in a
   fresh process is the whole point: it catches round-trip corruption through
   Postgres *and* non-deterministic extraction, neither of which an in-memory
   check can structurally detect.

~~**`storage_path` is repo-relative, resolved to absolute at read time**~~
**Superseded in Phase 2, slice 2**, exactly as this item anticipated it would be.
The column is now `storage_key` and holds a storage key, not a path; the
repo-relative rule and the `to_storage_path`/`resolve_storage_path` helpers are
gone. The reasoning that produced it survives the change: an absolute path is
machine-specific data written into the database, the same class of mistake as
hardcoding `localhost`. A storage key is machine-independent for a stronger
reason — there is no machine in it at all.

The test corpus is committed for a reason that has *not* changed: a repo that
cannot run its own `verify` on a clean clone fails the fresh-clone bar set in
Phase 0. That bar is also why slice 2 kept a local backend rather than making
Supabase the only one. The MIT OCW material is CC-licensed, so redistributing it
is fine, and it is the same corpus Phase 7's demo course uses.

**Known deviations and outstanding gaps**

- **Course term bounds (`ends_on >= starts_on`) are validated in the service, not
  by a CHECK constraint**, which is inconsistent with every comparable rule in the
  schema. Migration `0002` is already applied, and fixing this properly means an
  `0003` for a rule nothing depends on yet. Fold it in if `courses` is touched
  again for another reason; do not spend a migration on it in isolation.
- ~~**The re-ingest guard has no automated test.**~~ **Closed in Phase 2,
  slice 1**, on the amortisation condition this item set: Phase 2 needed database
  fixtures anyway, so `tests/conftest.py` and `tests/test_reingest.py` landed
  together. The suite runs against a separate `rewind_test` database, built by
  running the real migrations rather than `Base.metadata.create_all` — a suite
  that builds its schema from the models cannot notice the models and the
  migrations drifting apart.

**Done when**
- A real lecture PDF ingests end to end.
- For a random sample of chunks, `char_start`/`char_end` slice the source page
  text back to exactly the stored `content`. This is verified, not assumed — by
  all three layers above, `verify` included.
- No chunk has a null `page_number`, `char_start`, or `char_end`.
- Re-ingesting the same document does not create duplicates.

**Status — complete** (as of 2026-08-05)

Verified by running against the 6.006 DFS lecture, end to end:

- `users`, `courses`, `documents`, `chunks` migrated; `alembic downgrade base`
  then `upgrade head` confirmed clean from empty.
- CLI: `create-course`, `ingest` (with `--dry-run`, `--force`, `--no-embed`),
  `embed`, `verify`.
- All three verification layers pass. `verify` was proved able to *fail*, not
  just to pass, by injecting three faults into the database — a changed character
  in `content`, a `char_start` shifted by one, and a wrong `page_count` — and
  confirming each is caught and reported with the offset of the first divergence.
- Re-ingest refuses without `--force` and replaces rather than duplicates with it:
  three ingests of the same file leave one document and nine chunks.
- Embedding: 9 chunks at 1536 dimensions, unit norm, 9 distinct vectors, document
  at `ready`. **Partial failure was tested by injection, not assumed**: failing
  batch 2 of 3 left the 4 chunks from batch 1 committed, moved the document to
  `failed`, and printed the resume command; re-running embedded exactly the 5
  outstanding chunks and reached `ready`.
- Cost visibility: the chunk count, an estimated token count and an estimated
  dollar figure print before any request is made. The token estimate is
  characters ÷ 4, a deliberate approximation rather than a `tiktoken` dependency,
  and is labelled an estimate wherever it appears.

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

**Constraints settled before writing any of it**

- **Postgres is the record of intent; Redis is only the dispatch mechanism.**
  `POST /documents` writes the `documents` row *and* a `processing_runs` row at
  `queued`, commits, and enqueues only then. This is what makes Render's
  no-persistence free Key Value tier (see the deferred section) survivable
  without changing the architecture: if Redis drops the queue, the run row is
  still sitting at `queued`, so the work is **visible as outstanding** rather
  than evaporated. The failure this eliminates is the silent one — a user gets a
  document id back, Redis restarts, and the document sits at `pending` forever
  with no record that anything was ever meant to happen to it, which is
  indistinguishable from a bug.

  **No automatic re-drive sweeper is built.** The deferred section is right that
  the honest fix for lost jobs is a paid plan, not application-level
  compensation; a sweeper also introduces a concurrency question it would then
  have to answer (two sweepers, or a sweeper racing a live worker). Phase 2 makes
  stranded work visible. Re-driving it stays manual.

- **The CLI's `ingest` stays synchronous. It is not wrapped and not replaced.**
  It is the fallback for when the queue is the broken thing: if every path
  enqueues, a dead worker or a wrong `REDIS_URL` means there is no way to get a
  document into the database at all, and no known-good path to compare against
  while debugging. `verify` and `embed` are already direct-call for the same
  reason. What ROADMAP means by "the same service" is service reuse, not
  entrypoint reuse — `ingest_document` was written in Phase 1 with a
  caller-owned transaction precisely so both callers work.

  **One orchestrating service, `process_document`, owns the `processing_runs`
  lifecycle**, and both the CLI and the worker task call it. Otherwise a CLI
  ingest leaves no history while an uploaded one does, and the table tells two
  different stories about what happened. The worker task body stays a handful of
  lines, per `ARCHITECTURE.md`.

- **`documents.status` needs no migration.** `pending` already means "uploaded,
  nothing started", `processing` means a worker has it, `ready`/`failed` are
  terminal. The existing CHECK constraint covers all four. `documents.status` is
  *current state*; `processing_runs.status` is *history*, and they deliberately
  do not share a vocabulary: `queued`, `running`, `succeeded`, `failed`.

- **One `processing_runs` row per attempt, with `attempts` as the attempt number
  (1-based).** Attempt 3 is the third row, carrying `attempts = 3`. The
  alternative — one row per job with a counter — overwrites the first error with
  the second, which discards exactly the information worth having: a document
  that failed transiently twice and then succeeded should be able to tell that
  story. This resolves the ambiguity in `ARCHITECTURE.md`'s description, which
  says "one row per ingestion attempt" while also listing an `attempts` column.

- **Not every failure is retried, and the split is by exception type.**
  `ServiceError` — our own, meaning no text layer, no such course, chunks present
  without `--force` — is **permanent**: it fails identically on all three
  attempts, so retrying burns time *and* produces three failed rows that make a
  deterministic problem look like flakiness. Anything else (network, OpenAI 5xx
  or 429, a dropped connection) is **transient** and retries with `max_tries = 3`.

- **arq does not retry on an arbitrary exception, and this bullet said it did.**
  Corrected in slice 3 after the wiring was tested against a real transient
  fault. arq retries on `Retry`, `RetryJob` and `CancelledError`; every other
  exception marks the job finished and failed (`worker.py`, `run_job`).
  `max_tries` *bounds* retries that were requested — it does not create them.

  The consequence of the original reading is the exact inversion of the rule
  above: letting an exception propagate, which reads like the obvious way to say
  "this failed, try it again", yields one attempt and no retries, turning every
  transient failure permanent. So `process_document_task` catches broadly and
  raises `Retry(defer=2 ** job_try)` — 2s then 4s — while `ServiceError` returns
  instead. **Permanent is now the path that returns; transient is the path that
  raises `Retry`.**

  The ceiling is checked in the task rather than left to arq, so the third
  attempt re-raises and fails as itself. Deferring past the ceiling instead makes
  arq drop a phantom fourth attempt with a "max retries exceeded" line and no
  exception to read. Verified by fault injection — a worker pointed at an
  unresolvable storage host produced exactly three `processing_runs` rows, at
  +0s, +2s and +6s, each carrying the connection error.

- **The permanent/transient split is enforced at the point the library raises,
  not at the point the decision is read.** A malformed PDF surfaces as
  `pdfminer`'s `PdfminerException`, which is not a `ServiceError` and would
  therefore have been classified *transient* and retried three times — the exact
  outcome the rule above exists to prevent. `extract_pages` translates it into a
  `ServiceError` instead. The general rule this instance illustrates: any
  library exception that is deterministic for a given input has to be translated
  where it is raised, because the retry classifier upstream has no way to tell it
  apart from a dropped socket. The translation also produces the readable message
  the phase's done-when bar asks for, rather than a traceback.

- **Retry is safe because Phase 1's design is already idempotent**, and this is
  load-bearing rather than incidental: at-least-once delivery means the task body
  *will* sometimes run twice on the same document. `ingest_document` with
  `force=True` deletes and rebuilds chunks in one transaction, and
  `embed_document`'s work list is "chunks where `embedding IS NULL`", so attempt
  2 re-embeds only what attempt 1 did not and nothing is billed twice. That
  resumability was built for a different reason and pays off here.

- **A precondition failure records no run.** Unknown course, a path outside the
  repo, existing chunks without `--force` — these are complaints about the
  *request*, not processing attempts, and they raise before any run row is
  opened. Otherwise the history fills with rows that never represented work, and
  "how many times has this failed" stops meaning anything. This is why
  `resolve_document` is split out of `ingest_document`: the run boundary sits
  between them, and the document row has to exist before the failure-prone part
  starts, or a PDF that cannot be parsed leaves no history at all.

- **Progress is reported through `logging`, not `print`.** The CLI and the worker
  need the same lines, and a background job writing to stdout is reporting into a
  void. The CLI raises the level for the `app` logger alone rather than the root
  logger — httpx logs every request at INFO, which would put a line of URL noise
  between each pair of lines worth reading.

- **A document is never left at `processing` with no live run.** On failure it
  moves to `failed` in its own transaction with the error recorded on the run
  row, and `ready` is set only when a count confirms no chunk is missing a
  vector — never inferred from the task returning. Same rule as Phase 1's
  embedding step, for the same reason.

- **Crash recovery and crash detection are separate mechanisms, on purpose.**
  Recovery is arq's: it holds an in-progress key while a job runs, and a
  hard-killed worker's job becomes re-runnable once that key expires, bounded by
  `max_tries`. Detection is ours, because arq's mechanism lives in the same Redis
  that may vanish. `GET /documents/{id}/status` derives a `stale` flag when the
  latest run is `running` and `started_at` is older than a threshold — computed
  on read, so it costs nothing until someone looks, and the polling UI is the
  thing that looks. The threshold is **measured against a real job, not
  guessed**, and recorded here with the measurement beside it. (This said "slice
  2" when it was written, before the storage slice was inserted ahead of the
  worker. It is slice 3 — there is no job to measure until the worker exists.)

- **Supabase Storage lands in this phase**, as a slice before the worker. The
  forcing argument is not tidiness: `POST /documents` receives bytes, the worker
  is a separate process with its own filesystem (a separate Render service), and
  the free tier has no shared persistent disk — so a `storage_path` pointing at
  local disk means the deployed upload path works on no machine but the
  developer's. That breaks this roadmap's own rule that every phase ships and
  `main` is always deployable. Deferring it also means building the upload UI
  twice, and the second version gets less testing than the first. Phase 1 already
  anticipated this: `storage_path` becomes a storage key here, and `verify`
  changes from reading disk to downloading.

**Settled in slice 2 (storage)**

- **Two backends, both real, chosen by `STORAGE_BACKEND`.** `local` writes under
  `backend/.storage`; `supabase` is what the deploy runs on. This is not
  speculative abstraction, which the protocol-with-one-implementation rule would
  otherwise catch: both are used, and the local one is what makes a fresh clone
  able to run `ingest` and `verify` with no credentials — the Phase 0 bar. The
  default is `local` so that path is the one a newcomer hits first.
- **Keys are `{user_id}/{filename}`, deliberately not content-addressed.** Under
  a content hash, re-exporting a lecture with one slide changed is a different
  key and therefore a second document, silently orphaning the first: a concept's
  timeline splits across a semester and nothing errors. Keying on the filename
  makes the same lecture the same document, which is what
  `UNIQUE (user_id, storage_key)` and `--force` already assumed. The accepted
  cost is that two different files with the same name collide — loudly, as
  "already exists", which is the failure worth having. Pinned by
  `test_the_same_file_under_a_different_name_is_a_different_document`.
- **`storage_path` was renamed to `storage_key` (migration `0004`), not just
  reinterpreted.** A column named `_path` holding a key documents the confusion
  instead of removing it, and the rename was never going to be cheaper: one
  development row, no production data, no type change. No data migration — a path
  is not a key and no expression converts one to the other; the stale rows were
  re-ingested.
- **Supabase Storage is called over its REST API with `httpx`,** not through the
  `supabase` client. It is two endpoints. `httpx` was already in the tree beneath
  `openai` and is now declared directly, because this code calls it.
- **The service key must be sent in the `apikey` header, not only
  `Authorization`.** Found by the live round trip this slice insisted on, not in
  production. The two key formats authenticate differently: a legacy
  `service_role` key is a JWT that storage-api validates from `Authorization`
  itself, while a current `sb_secret_...` key is not a JWT at all and is resolved
  by the API gateway from `apikey`. Sending only `Authorization` with the latter
  fails as `Invalid Compact JWS` — a message about token *shape*, which reads
  like a corrupted secret rather than a missing header, and would have cost hours
  to diagnose against a deployed worker. This is the same shape of failure as
  Phase 0's Redis region mismatch, caught for the price of one test upload.
- **A missing object is `ServiceError`; a 5xx or a dropped connection is not.**
  Same split `extraction.py` makes, for the same consumer: the retry classifier
  sees an exception type and nothing else. An object that is not there will not
  be there on attempt 3, and neither will a 60MB file the bucket's size cap
  rejects — permanent. Retrying those three times makes a settled fact look like
  flakiness.
- **Uploading is the caller's job, not `process_document`'s.** Slice 3's worker
  can only ever be handed a key, so the CLI uploads and then calls the same
  service the worker will. Accepted wart: an upload whose request then fails a
  precondition — an unknown course, say — leaves the object behind. Phase 2 has
  no delete path and does not grow one for this.
- **The test suite can never reach the real bucket.** The `storage` fixture is
  autouse and forces `local` at a `tmp_path`, with
  `test_the_suite_never_reaches_the_real_bucket` asserting it. A suite that
  *can* write to production storage eventually will. The consequence is that
  `SupabaseStorage` has no automated test — it is exercised by hand, and the
  round trip above is that exercise.

**Settled in slice 3 (worker + upload routes)**

- **`submit_document` resolves before it uploads, and enqueues last.** Four steps
  and every boundary is chosen: resolve (preconditions raise here, so a bad
  request becomes a 4xx with nothing enqueued and no run row), upload, commit,
  enqueue. Enqueueing last is not stylistic — a worker can claim a job the
  instant it is queued, so a job whose document row is still uncommitted gets
  handed a document the worker cannot see. This is where "a precondition failure
  records no run" lands on HTTP exactly: a request that was never valid leaves no
  history of an attempt, because there was no attempt.

  Note the CLI does the opposite — it uploads *before* resolving — and both are
  right. The CLI has no separate resolve step to put first; the API does.

- **A failed enqueue is a 5xx, and re-uploading is the repair.** The document
  sits at `pending` with its bytes already stored, so the second upload resolves
  to the same row, finds no chunks and enqueues again. That works only because
  Postgres holds the intent and Redis merely carries the request — the same
  premise the no-persistence Key Value plan forced.

  Consequence for the error types: `ServiceError` had to gain `NotFoundError` and
  `ConflictError` subclasses, because HTTP needs 404 vs 409 vs 400 where the CLI
  needed only "the user did something wrong". The rule holding it together is
  that **every `ServiceError` is a 4xx** — the caller can fix it by definition —
  so the route maps the class to a status code with a 400 default and no 5xx
  entry. The one server-side failure on that path, arq returning no job, raises
  `RuntimeError` rather than `ServiceError` precisely so it does not land in the
  4xx table. Add a subclass when an entrypoint has to *act* differently, never to
  categorise.

- **The 409 guard means "chunks exist now", not "no job is in flight".** Found by
  accident: a re-upload sent while the first job was still running returned 202,
  not 409, because at that instant the document had no chunks yet. Both jobs then
  ran against the same document and it ended `ready` at attempt 2 with 9/9 chunks
  — correct, and only because the task always re-ingests with `force=True` and
  `embed_document`'s work list is "chunks where `embedding IS NULL`". The
  at-least-once idempotency built in Phase 1 is what absorbs this.

  Left as is. Closing the race properly means locking the document row for the
  duration of the request or checking for a live run, and the failure it prevents
  is a duplicate upload of the same file within a few seconds that already
  produces the right answer. Recorded so it is a known property rather than a
  surprise the first time `attempts = 2` shows up on a document nobody retried.

- **Status reports counts, never a percentage.** `chunks_total`,
  `chunks_embedded`, `status`, `attempts`, `run_status`, `error`, `stale`. Until
  chunking finishes there are no chunks, so any single number would have to
  invent progress for the extraction phase — and an invented number gets rendered
  confidently and debugged later as if it meant something. Same principle as
  Phase 1 ending status at `processing` rather than `ready`.

  A failed document is **200**, not 4xx: the request to *know* succeeded. Only an
  unknown document is a 404. A 4xx here would fire a client's error handling on a
  perfectly good answer and hide the error being reported.

- **The stale threshold, measured.** `stale_run_after_seconds = 960`. Two
  independent constraints, and the binding one was not the obvious one:

  - *Lower bound, from arq.* Its in-progress lock is
    `psetex(job_timeout + 10s)` (`worker.py`, `in_progress_timeout_s` and the
    `psetex` in the poll loop). Until it expires no worker may re-claim the job;
    after it expires any worker does. With `job_timeout = 900` that is 910s, so a
    threshold below it would report `stale` on a job arq is about to pick up by
    itself. **Measured:** a worker killed mid-job with `job_timeout = 15` had its
    job re-claimed after 25.18s — `15 + 10`, exactly.
  - *Ruled out, from a job duration.* A real end-to-end run of the 5-page test
    lecture took 3.9s for 9 chunks with embedding — 0.44s per chunk, dominated by
    OpenAI round trips. A 600-chunk document is therefore minutes, so any
    threshold sized from an *observed* job calls healthy long documents dead.
    Measurement's job here was to eliminate the approach, not to supply a number.

  960 clears 910 with 50s spare. The cost of being right rather than fast: a
  hard-killed worker is not flagged for ~16 minutes, which is acceptable because
  nothing acts on the flag — recovery is arq's, and `stale` is only a hint for
  the UI.

- **A crash consumes one of the three tries.** The re-claimed job came back as
  `try=2`, not as a fresh attempt. So `max_tries = 3` is a budget shared between
  transient failures and worker deaths, and three crashes on the same document
  exhaust it. Left as is: a document that has killed three workers is not one to
  keep feeding them.

- **The crashed run row is left stranded at `running`, deliberately.** Nothing
  closes it, because the process that would have was killed — that row *is* the
  evidence of the crash. It stays readable because `latest_for_document` orders
  by `attempts`, so the status endpoint always answers from the live attempt
  while the stranded one remains as history. Ordering by `attempts` rather than a
  timestamp also matters on its own: `UNIQUE (document_id, attempts)` is what
  makes the attempt number strictly increasing, whereas two runs opened in the
  same millisecond can tie on `started_at`.

- **A graceful restart must not look like a permanent failure.** arq cancels
  running tasks on shutdown and `CancelledError` is one of the three exceptions
  it re-enqueues on — which works only because it inherits from `BaseException`,
  so the task's broad `except Exception` does not catch it. That is a property of
  the class hierarchy rather than of anything written here, so it is pinned by a
  test: narrowing that handler some day would silently turn every deploy into a
  batch of permanently failed documents.

- **A document is `pending`, not `processing`, while its first run is `running`.**
  `process_document` moves the status only once ingestion has produced something.
  Not worth a status write to fix — `run_status` already tells a client the
  difference, and the endpoint returns both.

- **The worker's log handler goes on the `app` logger, not the root.** arq
  installs a handler on the `arq` logger only, so the root has none and every
  service log line is dropped — the worker runs correctly and says nothing. The
  obvious fix, `basicConfig`, prints each arq job line **twice**, because arq's
  own records propagate up to the new root handler. A `StreamHandler` on `app`
  with `propagate = False` is what gives one copy of each.

**Settled in slice 4 (upload UI)**

- **`Progress` is deliberately not installed in `components/ui/`, and this line
  exists so nobody adds it back.** shadcn is added in this slice with exactly two
  components, `Button` and `Card`. A progress bar is the obvious third, and it is
  omitted on purpose: the status endpoint returns counts rather than a percentage
  because until chunking finishes there are no chunks, so any single number would
  have to invent progress for the extraction phase. Having a `Progress` component
  sitting in the tree is how that invention happens later — someone wires it to
  elapsed time, it animates, and it is debugged in Phase 4 as if it meant
  something. The bar that *is* rendered during embedding is eight lines of markup
  driven by `chunks_embedded / chunks_total`, and it exists only where both
  numbers are real. Install `Progress` when there is a real ratio it can show.

- **The stage a document is in is a function of counts, not a status field.**
  `processing` splits into "Extracting text" and "Embedding — 3 of 9" on
  `chunks_total === 0`, because that is the only signal distinguishing them. No
  `extracting` status was added: it would be a fifth value in a CHECK constraint
  to encode something already derivable, and `documents.status` is deliberately
  current state rather than a progress log.

  Extraction therefore gets an indeterminate spinner and **no bar at all**. The
  honest rendering of "we do not know how far along this is" is not a bar at 10%.

- **`stale` renders as an addition to the stage, not a replacement for it.** The
  flag exists to name a job whose worker is gone; a row that swapped to "Stalled"
  would lose the information that it stalled *during extraction*. So the stage
  line stays and an amber note appears under it naming the attempt number. This
  is what makes the flag worth computing — it was already correct on the wire in
  slice 3 and invisible, which is the same as absent.

- **A refused upload is a different shape from a failed document, not a row with
  a fake status.** A 409 or 404 means nothing was enqueued and no run row exists,
  so there is no document to poll. Modelling it as `failed` would file "you
  already uploaded this" next to "this PDF is broken", and the two have different
  repairs. `RejectedUpload` carries the server's `detail` verbatim and nothing
  else.

- **Polling stops at `ready` and `failed`.** `refetchInterval` returns `false` on
  a terminal status rather than running forever at a slower interval. Worth
  knowing when testing by hand: once a row reaches `ready`, changing the row in
  the database will not move the UI, because nothing is asking any more.

- **`GET /courses` is part of this slice.** A UUID pasted into a text box is not
  an upload page, and the phase's own done-when cannot be demonstrated through
  the UI without it. Courses are still *created* by the CLI, where term bounds
  get entered deliberately rather than typed into a form field. The route brought
  `api/deps.py` with it: a second router importing `get_session` from
  `api/documents.py` makes the two depend on each other in an arbitrary
  direction. `ingestion.list_courses` is a three-line pass-through to the
  repository and is kept anyway, because the alternative is a route importing a
  repository and a layering rule that holds everywhere except where it was
  inconvenient.

- **No Zustand this slice — a decision, not an oversight.** The list of uploads is
  one page's state with one consumer, and React Query already owns the per-row
  server state. Zustand stays in the stack for state genuinely shared across
  routes. The list is also session-scoped on purpose: it records what *this* visit
  uploaded. A page listing every document belongs to a later phase, and
  pretending this is one would make a reload look like data loss.

**Build order**

Four slices, ordered so that each one's failure mode is distinguishable from the
previous one's. Building the worker and the endpoint together and finding a
document stuck tells you nothing about whether the lifecycle logic or the queue
wiring is at fault.

1. ~~**`processing_runs` + `process_document`, driven from the CLI.**~~ **Done.**
   No arq, no HTTP. The failure semantics get boring before anything
   asynchronous touches them.
2. ~~**Supabase Storage.**~~ **Done.** `storage_path` became `storage_key`;
   `verify` downloads. Findings above.
3. ~~**arq worker + `POST /documents` + `GET /documents/{id}/status`.**~~
   **Done.** Demonstrable with curl and no frontend, which is where the
   concurrent-upload and killed-worker criteria got tested. Findings above.
4. ~~**`features/upload/`**~~ **Done.** Drag-drop plus polling progress, and
   `GET /courses` so the page can name what it is uploading into. Findings above.

Slice 1 is also where `tests/` gets database fixtures, so Phase 1's outstanding
re-ingest test lands with it — that is the amortisation condition Phase 1 set for
it, rather than paying the fixture cost for a single test.

**Known deviations and outstanding gaps**

- **`features/upload/` has no automated test, so the no-invented-percentage
  guarantee rests on one manual verification.** `describe()` in `UploadRow.tsx`
  is the whole state table and it is a pure function of `DocumentProgress` — the
  obvious thing to pin, and cheap to pin, once there is anything to run it with.
  What it should assert is what was checked by hand: `processing` with
  `chunks_total === 0` produces **no** `ratio`, and a ratio is never derived from
  anything but the two counts.

  The risk this records is the same one the `CancelledError` test exists to
  cover. Both guarantees are invisible in the code that depends on them: nothing
  about `describe()` announces that returning a `ratio` during extraction would
  be a fabrication, so a future session tidying the branches could collapse them
  and produce a bar that looks fine and means nothing. The difference is that the
  arq behaviour is pinned and this is not.

  **Do not add a test runner for this alone.** The amortisation condition is the
  same one Phase 1 set for the re-ingest test that waited for database fixtures:
  add it when the frontend independently justifies one, most likely Phase 4 when
  the timeline UI lands and there is real client-side logic to test. Write these
  assertions in the same slice that installs the runner.

  **Closed in Phase 3, slice 5**, one phase earlier than guessed — the dating UI
  justified the runner. `describe()` is exported and both assertions are written,
  including one that moves `attempts`, `run_status` and `stale` and checks the
  ratio does not follow. Mutation-checked. See Phase 3's "Settled in slice 5".

**Done when**
- Uploading through the UI produces a document that reaches `ready` without any
  manual step. — **Met.** Driven in a real headless Chrome over CDP, with
  `DOM.setFileInputFiles` handing the hidden input actual PDFs, so the path
  exercised is the page's own: `Queued` → `Embedding — 0 of 9` → `Ready — 9
  chunks` in 3.5s, no console errors, no manual step.
- A deliberately corrupt PDF ends `failed` with a readable error in
  `processing_runs`, and the API says so. — **Met.**
- Two documents uploaded at once both complete. — **Met.** Two concurrent
  uploads, distinct document rows, both `ready` at 9/9. Re-proved through the UI
  in slice 4: two files dropped together, both rows `Ready — 9 chunks` at t+3.5s.
- The worker survives a restart mid-job without losing the document. — **Met.**
  Hard-killed mid-run (document `pending`, run 1 `running`, 0 chunks), restarted,
  re-claimed after 25.18s as attempt 2, `ready` at 9/9. See the stale-threshold
  and stranded-run notes above.

**Status — complete** (as of 2026-08-09), tagged `phase-2-complete`.

Every state a row can render was checked against a real browser rather than
reasoned about, the fast ones held still by constructing them in the database
while the page polled: `Queued`; `Extracting text` with **no bar element at
all**; `Embedding — 3 of 9` with the bar measured at 33.3333%; `Ready — 9
chunks`; `Failed` carrying the backend's error verbatim (`could not read … No
/Root object!`); the 409 refusal as a separate red row; and the amber stale note,
forced by backdating a `running` run past `stale_run_after_seconds`. No label
contained a percentage — asserted against the DOM's rendered `style.width`, not
against the label text, so a bar at a fabricated width would have been caught.

The browser was driven over CDP (`--remote-debugging-port`), with
`DOM.setFileInputFiles` handing the real hidden input real PDFs, so what ran was
the page's own path rather than a simulation of it.

**Corrected after the phase closed** (2026-08-09)

Two gaps, both found by asking whether `main` was actually deployable and then
reading the code instead of this document. Neither was a decision anyone made;
both were things this file already asserted as settled and nothing implemented.

- **Gap 1 — the worker had no deployed service.** Phase 2's plan listed "a second
  service for the worker on Render" and it was never created; only the Phase 0
  web service existed. The deployed app therefore accepted uploads, returned a
  document id, and ran nothing — every document sat at `pending` forever, which
  is the exact silent failure the constraint at the top of this phase was written
  to eliminate, reintroduced by the deploy rather than by the code.

  Fixed by `render.yaml` at the repo root, in the repo rather than the dashboard
  because dashboard-only config is what cost time in Phase 0: the same-region Key
  Value rule existed nowhere until it broke. Two things the file records that were
  otherwise tribal knowledge: **a blueprint does not adopt dashboard-created
  services**, so applying it unchanged produces a second API next to the hand-made
  one, and deleting the hand-made one changes the `onrender.com` hostname that
  Vercel baked into `NEXT_PUBLIC_API_URL` at build time; and the Key Value
  instance is deliberately *not* declared, because a blueprint entry would
  provision a second empty Redis while the real queue sat elsewhere.

  **One service runs both processes, not two.** The obvious shape is a `web` and a
  `worker`, and it was written that way first — then discarded, because
  **`type: worker` is not offered on Render's free instance type** and the Starter
  plan it requires is not worth $7/month for a project with no users. `start.sh`
  runs uvicorn and arq side by side instead. What that costs, accepted knowingly:

  - The worker sleeps when the free instance spins down, so **recovery stops being
    automatic**. A stranded run needs a live worker to re-claim it and there is no
    worker while the service is asleep, so it waits for the next visitor. Gap 2's
    `queued` row and `stale` are what keep that visible rather than silent, which
    is what makes it survivable at all.
  - Both processes share 512MB. **This is the risk most likely to actually bite** —
    uvicorn, arq, pypdf holding a large PDF and embedding batches in one cgroup,
    where an OOM kill on a big upload now takes the API down with it.
  - Either process dying takes the container down. Deliberate; see below.

  Two things about the supervisor are load-bearing and neither is obvious.
  **`start.sh` no longer `exec`s**, reversing what its own comment used to say.
  That comment was right that a shell at PID 1 swallows SIGTERM, but the cause is
  having no handler, not being a shell: Docker signals PID 1 and nothing else, so
  `arq &` followed by `exec uvicorn` leaves arq unsignalled and hard-killed on
  every deploy and every spin-down. Trapping and forwarding is the only way arq
  gets to finish its in-flight job. **And the supervisor exits when *either* child
  exits**, rather than waiting for both. If arq dies alone, uvicorn keeps serving,
  `/health/ready` passes, the dashboard is green, and uploads are accepted and
  never processed — this gap's exact failure mode, reintroduced in a shape nothing
  can observe. Exiting non-zero converts that into a restart loop, which is noisy
  and visible. No HTTP health check can cover the worker; `stale` on the document
  status endpoint remains the only signal that the queue stopped draining.

  **What splitting them back out takes**, if the project ever earns it: restore
  `backend/start-worker.sh` from commit `d389630`, add a second `type: worker`
  service to `render.yaml` pointing `dockerCommand` at it, and move the shared
  env vars into an `envVarGroup` both services read — the drift failure there is
  silent, since an API on `supabase` and a worker on `local` both start healthy
  while every job fails against a disk the uploader never wrote to. Migration
  ordering also becomes a live question again: only one service may run
  `alembic upgrade head`, because Alembic holds no lock across an upgrade. That
  script is deleted rather than kept dormant, per the no-code-for-later rule.

  Verified in the built image against real Postgres and Redis, not reasoned about:
  both processes alive under the shell at PID 1 (`/proc` listing arq at 8 and
  uvicorn at 9) with the migration applied ahead of them; `docker stop` producing
  `shutdown on SIGTERM ◆ 0 ongoing to cancel` from arq **and** a clean uvicorn
  shutdown, exit 0 in 2.3s; `kill -9` on arq alone taking the container down with
  exit 1; and an upload posted to the container processed by the worker inside it,
  reaching `attempts 1, succeeded` with 9 chunks.

- **Gap 2 — `submit_document` never wrote the `queued` run row.** This is the
  first constraint listed under this phase, quoted there as "writes the
  `documents` row *and* a `processing_runs` row at `queued`, commits, and
  enqueues only then". It did not. It uploaded, committed the document, enqueued,
  and returned; the first `processing_runs` row was inserted by the *worker*, at
  `running`. So the entire mechanism that makes Redis disposable was absent: a
  dropped job left a document at `pending` with no run at all — indistinguishable
  from one uploaded a second ago, and unreachable by `stale`, which only measured
  from `started_at` and so could never fire on work no worker had touched.

  The fix is four changes and one new query. `submit_document` opens the run at
  `queued` before its commit. `process_document` calls a new
  `processing_runs.claim_queued`, moving that row `queued` → `running` rather than
  inserting a second one beside it — inserting would both double-count the attempt
  and strand the queued row as permanently outstanding work that had in fact been
  done. It returns `None` for the two paths that legitimately have nothing to
  claim (the CLI, and a retry whose queued row the previous attempt already
  closed), and the caller opens a fresh run at `running` in that case. A failed
  `enqueue_job` now closes the run through `mark_failed`, since an undispatched
  job is not owed work. And `document_progress` measures a `queued` run's silence
  from `created_at`, because `started_at` is null by CHECK constraint — without
  that, the lost-job case the row exists to expose would read `stale: false`
  forever.

  One constraint interaction worth keeping: `ck_processing_runs_started_at_matches_status`
  reads a null `started_at` as "still queued", so closing a run that never started
  was impossible until `mark_failed` began filling it with
  `COALESCE(started_at, now())` — coalesce rather than an unconditional `now()`,
  so a run that did start keeps the time it really started.

  Verified live against the production shape rather than only in tests: with the
  API up and **no worker running at all**, an upload answered
  `attempts: 1, run_status: "queued"` where it had previously answered
  `null, null`; backdating that row past `stale_run_after_seconds` flipped
  `stale` to `true`; and starting a worker drained it to a single row reading
  `attempts 1, succeeded` — not two. Suite is 53 tests, three of them new. The
  rewritten one is the tell: `test_status_before_a_worker_has_touched_it`
  previously asserted the run fields were **null** at that point, which encoded
  the bug as the requirement.

**The meta-observation, which is the durable part.** This is the second time a
claim in this file turned out to be intent rather than implementation. The first
was arq's retry semantics — recorded as settled, acted on, and only later checked
against arq's source. Both propagated into code confidently *because* the
surrounding prose read as decided fact, and prose does not distinguish "we
concluded this" from "we built this". The lesson is not to write less down; it is
that a settled decision and a verified behaviour are different claims, and the
ones with consequences are worth stating in a form that can fail — a test, or an
observation with a number in it. Where that is not possible, the "Verified live"
paragraphs above are the fallback: say what was run and what came back, so a
later reader can tell what was checked from what was merely agreed.

**Verified in production** (2026-08-10)

Everything above was proved on a local stack — Phase 2 closed against Docker
Compose, and both gap fixes were checked in the built image against local
Postgres and Redis. What had never happened was a real upload through the
deployed page. It has now: a PDF dropped on the Vercel frontend reached
`Ready — 9 chunks`.

What that upload establishes, ordered by how much it was actually in doubt:

- **Gap 1 is closed where it mattered.** The failure it fixed existed only in the
  deploy — code that worked locally and ran nothing in production. A job
  dispatched by the API process and executed by the arq process inside the same
  free-tier container is the only observation that can close it, and it is
  precisely the one the built-image check could not make.
- **Embedding reaches OpenAI from Render.** `ready` is unreachable until every
  chunk has a vector, so `9 chunks` is nine round trips out of the container on
  the key Render holds.
- **The Supabase storage backend works** — bytes uploaded by the request handler
  and downloaded again by the worker. Note what this does *not* prove: the
  one-service topology gives both processes the same filesystem, so
  `STORAGE_BACKEND=local` would have looked identical here. The reason for
  `supabase` is still the one `render.yaml` gives — Render's disks are ephemeral
  and a redeploy would orphan every `documents` row — not anything this run
  demonstrated.

**Gap 2 needed a second run, because the upload above could not show it.** The UI
reports the document's terminal state and never the `processing_runs` row behind
it, so a successful upload looks identical whether or not `submit_document` wrote
the `queued` row. Watching it required `GET /documents/{id}/status` in the window
between the enqueue and the claim.

**That window is arq's poll delay, and nothing else.** The intuition to reach for
is the free instance spinning down — upload while it wakes and catch it with no
worker yet. It does not work, and the reason is in `start.sh`: the order is
`alembic upgrade head`, then `arq &`, then `uvicorn &`. **uvicorn comes up last**,
so any request that gets served at all arrives after arq is already polling.
There is no worker-less state reachable over HTTP. What is left is the gap
between `submit_document` committing the run and arq's next poll —
`poll_delay` is arq's default 0.5s, since `WorkerSettings` sets only `max_tries`
and `job_timeout`. So this is a race to be retried, not a condition to wait for:
POST and GET back to back in one process, repeat until it lands.

It landed on the first attempt (document `a97a4381`). The POST took 2203ms — a
cold start — and the GET returned 578ms later:

```
status='pending' run_status='queued' attempts=1 chunks=0/0 stale=False
```

`run_status: "queued"` with `chunks 0/0` is the row Phase 2's first constraint
always claimed was written and for a while was not: written by the request
handler, before any worker touched the document. Polled to completion, the same
document read:

```
status='ready' run_status='succeeded' attempts=1 embedded=9/9
```

**`attempts` staying at 1 across both readings is the second half of the fix.**
`claim_queued` moved that row `queued` → `running` rather than inserting a second
run beside it — had it inserted, this would read 2, the attempt would be
double-counted, and the queued row would be stranded as outstanding work that had
in fact been done.

Both gap fixes are now observed in production rather than inferred from local
runs. That matters most for the parts that only exist for failure: `stale` is
measured from a `queued` run's `created_at`, and a job dropped by the persistence-
free Key Value instance is visible only because this row is there to be left
behind.

**The run could not start until a course existed.** Production had none, so
`create-course` was run against its database with the session-pooler URL that
lives only in Render's dashboard, creating 6.006 (`2020-02-03..2020-05-12`).
That there is no other way to do this is filed under Deferred — "a freshly
deployed environment is unusable through its own UI" — rather than treated as a
detail of this run, because it outlives the run and collides with Phase 7.

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

**Settled in slice 1 (the funnel + manual override)**

`services/dating.py::redate_document` is the single writer. `PATCH
/documents/{id}/date` is the `manual` path through it, and the two heuristics
become callers in slices 2 and 3 rather than writers.

**The funnel is guarded by a test, not by convention.**
`tests/test_occurred_at_sole_writer.py` parses every module under `app/` with
`ast` and asserts two things: nothing outside `repositories/documents.py` writes
the column, and nothing outside `services/dating.py` calls the function that
does. It looks for the four shapes a write can take — `Document(occurred_at=…)`,
`.values(occurred_at=…)`, `x.occurred_at = …`, and raw SQL naming the column in
an `UPDATE`/`INSERT` — rather than for the identifier anywhere, because a check
that fires on *reading* the value gets muted within a week.

Its limit, stated so nobody over-trusts it: it reads source, not behaviour. A
column name assembled at runtime walks past it. It guards the accident — a later
session adding `occurred_at=` to a nearby query because that is where the data
happened to be — which is the failure that actually occurs. ARCHITECTURE's
asymmetry note is amended to match: "no structural guard" was always about
holding two copies equal, not about who may write, and Phase 5's deferred
constraint trigger covers the harder half.

**Two tests exist to prove the guard is wired up.** One plants the three write
shapes and asserts it sees exactly three. The other plants a docstring saying the
code updates `occurred_at` and asserts it sees none — which is not hypothetical.
The first run of the check failed on `redate_document`'s own docstring, because
uppercasing the text before looking for `UPDATE ` matched the word "update" in
prose. Docstrings are now excluded and that exclusion is pinned by its own test.

**The general lesson is about what a check pays people to do, and it outlives
this regex.** A guard is an incentive, not just a filter: whatever makes it go
green is what the codebase will drift toward. This one, as first written, could
be satisfied two ways — narrow the pattern, or delete the sentence explaining the
invariant. The second is faster, requires no thought about the matcher, and
leaves a green run behind it. It is the wrong repair, and it is the *tempting*
one, which is precisely why noticing costs something.

So the failure mode to watch for is not "false positive" but **a check whose
cheapest fix damages the thing the check exists to protect**. A rule against
writing a column that fires on explaining the rule; a coverage threshold met by
deleting the untested branch; a lint against complexity satisfied by splitting a
function nobody can now follow. When a guard fires, the question is not only "is
this a real hit?" but "what is the laziest way to make this stop, and what does
that way cost?" If the cheap fix is the destructive one, the guard is
mis-specified and fixing the guard is the work — not paying its toll.

**`redate_document` does real work this phase, deliberately.** It owns the
term-bounds check. Without it the function would be a wrapper around one UPDATE
whose only justification is a Phase 5 write that does not exist — exactly the
shape a later session inlines. It also owns its transaction, because Phase 5's
cascade must be atomic with the date write and a boundary every caller has to
remember is one that a caller will forget.

**A `date` in, a timestamp at midnight UTC out.** All four sources are
day-granular — a syllabus names a day, a filename names a day, a person picking a
date picks a day — so accepting a datetime would invite callers to invent a time
of day no source knows. Server-local midnight was the alternative and would make
Phase 4's ordering depend on where the process runs.

**`filename_date` added as a fourth source** (migration `0005`), deviating from
the three this phase originally listed. `2024-09-14.pdf` is a date *read*; `Lecture
07.pdf` is a date *interpolated* against term bounds, and it goes systematically
wrong the moment a term has a reading week or two lectures in one week. Filing
both as `inferred_filename` blinds the UI to the difference exactly where
`occurred_at_source` is supposed to be honest. `inferred_filename` keeps its name
and narrows to the interpolated case; nothing had ever written it, so there was no
data to migrate. **No numeric confidence score anywhere** — `0.73` is
unfalsifiable and reads as authoritative, and this is the same failure Phase 2
refused when it kept `Progress` out of `components/ui/`.

**Out-of-term dates are refused, except manual ones.** An inferred date outside
the course's bounds is the heuristic being wrong, and a wrong date is worse than
no date. A manual one is different: the user is the authority on when their own
lecture happened, and refusing leaves a document nobody can fix — so it is
stored, returned with `outside_term: true`, and logged as a warning naming the
course and its bounds. That way a course with wrong term bounds shows up as a
pattern rather than as one odd document.

Verified live against the dev database, not only in the test database the suite
rebuilds from scratch: `0005` applied to a database with existing rows, and the
route answered `outside_term: false` in term, `outside_term: true` with the
warning line out of term, and 404 for an unknown document. Suite is 73 tests, 20
of them new.

**Carried in from Phase 2, slice 2 — `documents` has no `ON DELETE CASCADE`**

`fk_chunks_document_id_user_id` and the equivalent on `processing_runs` reference
the `(id, user_id)` pair without a cascade, so deleting a document is a
three-statement transaction — chunks, then runs, then the document — and a plain
`DELETE FROM documents` fails on a foreign key instead. Found by hand when a
stale Phase 1 row had to be removed after the `storage_key` rename.

Deliberately not fixed in Phase 2, which has no delete path: a migration for a
feature that does not exist is speculative.

**Corrected in Phase 3, slice 1 — Phase 3 is not where it stops being free.**
This item used to say it was, on the reasoning that "re-dating and re-processing
touch documents whose children may need rebuilding". Checked against Phase 3's
actual scope before starting it: syllabus parsing reads, filename inference reads,
the override endpoint updates one column, the UI displays and corrects. **Nothing
in this phase deletes a document.** The nearest thing is `force=true` re-ingest,
which has existed since Phase 2 and deletes *chunks* — the foreign key in question
is chunks→documents, so it is never exercised.

That is the same pattern as Phase 2's two gaps: a confident forecast about later
work that nobody re-checked when later arrived. A phase number is the wrong
trigger for a deferred item, because it comes due whether or not the condition it
was really about has happened. **The trigger is now a condition: the first code
path that deletes a `documents` row** — most likely a Phase 7 affordance for "I
uploaded the wrong file".

**Settled now, since deciding is free and the migration is not:** both children
cascade. `processing_runs` cannot meaningfully outlive its document —
`document_id` is NOT NULL under a composite foreign key, so "outliving" would
require a nullable column and would cost the ownership pin that composite key
exists to provide. Run history that must survive belongs in `learning_events`,
which is append-only and polymorphic with no foreign key, and which exists for
exactly this.

**Settled in slice 2 (filename inference)**

`app/services/filename_dates.py` holds three pure functions —
`read_explicit_date`, `read_ordinal`, `interpolate` — over a string plus the
course's term. No session, no I/O, deliberately: a hit rate is only meaningful if
it can be measured by calling the parser directly on a list of filenames, and a
parser that needs a database to answer a question about a string cannot be
measured that way. `services/dating.py` grows
`date_course_from_filenames`, which orchestrates them and routes every write
through `redate_document`. `date-course` on the CLI makes the slice runnable
before there is any UI.

**The two filename sources are genuinely different objects, which is why 0005
split the enum.** `2020-02-11-dfs.pdf` states a date — testimony, wrong only if
whoever named the file was wrong (`filename_date`). `lecture-07.pdf` states an
ordinal, and a date exists only after arithmetic performed on top of testimony
about something else (`inferred_filename`).

**Measured hit rate — 70 real filenames, 58 correct, 12 undated, 0 wrong.**

The corpus is the published resource filenames of MIT 6.006 Spring 2020, fetched
from MIT OpenCourseWare and committed as
`backend/tests/data/ocw_6006_s20_filenames.tsv` with a hand label per line. They
were not written for this test, which is the only reason the number means
anything: a corpus invented alongside a parser measures whether its author
thought of a case, not whether the parser works. The test that computes this
lives in the suite, so the three figures are pinned and a regression moves one.

The 12 undated are the three quizzes and three review sessions with their
solution files (`_q1`, `_review2_sol`). Neither `quiz` nor `review` is in the
keyword map, and both were left out *after* the measurement rather than added
before it — adding them would improve the number by construction and prove
nothing, since this corpus is also the only evidence that those shapes exist.

**Amended 2026-08-15: they were added, and the pinned figure is now 70 correct,
0 undated, 0 wrong. The twelve new rows are fitted, not measured.** Slice 5's
audit found these twelve were 100% of the extraction misses, which is a good
reason to close the deferral and no reason at all to read the new number as an
improvement — the sentence above still holds, and by construction is exactly
what happened. Nothing was learned about `quiz` or `review` by scoring a rule
against the rows that produced it; only a corpus from another course can say
whether those shapes generalise.

**What survives the fitting is 0 wrong on 70, and that is now the whole value of
the figure.** Clearing the three decoys (`6`, `006`, `20`) is a property of the
parser, not of its vocabulary, and the twelve new hits had to clear them like
every other row. The breakdown stays three-way for the same reason as before: an
aggregate would let `wrong` rise while `correct` compensates.

`review` was a keyword addition. `quiz` needed both — the word for `quiz1.pdf`,
and `q` alongside `l` and `r` in `_LETTER_ORDINAL`, because MIT writes `_q1` and
no amount of keyword matching finds `quiz` in it. `q` is the weakest single
letter in that class: `q3.pdf` plausibly means *question* 3. `_ORDINAL` is tried
first, so `ps5_q3.pdf` is pset 5 and never quiz 3, and that ordering is pinned by
the one fixture that can distinguish the two implementations — `ps5_questions.pdf`
cannot, since `q` requires digits behind it. A bare `q3.pdf` from a course where
`q` means question is not covered and is not coverable from the filename: the
ordinal is still right and only the kind is wrong, which strands the file in its
own interpolation sequence. That costs a worse candidate date and never a stored
one, since `inferred_filename` does not write `occurred_at`. The trade is written
next to the regex rather than only here, because that is where someone will be
reading when it matters.

**What that number does not cover, stated plainly because the shape of the gap
matters more than the figure.** These filenames carry ordinals and no dates. So
this measures *extraction* — surviving the three decoy numbers (`6`, `006`, `20`)
that sit in front of every real ordinal — and says nothing about whether an
interpolated date is right. The date half is unmeasured. OCW's calendar page is
client-rendered and would not yield lecture dates to four separate fetch
attempts, and inventing the dates of real 2020 lectures to score against would be
the exact fabrication this phase exists to prevent. **Until a real
lecture-date set exists, no claim about `inferred_filename` date accuracy is
supported by evidence.**

**Two reasons to expect interpolated dates to be worse than the extraction
figure suggests**, both structural rather than measured:

1. *Real timetables are not evenly spaced.* A Tuesday/Thursday course alternates
   2- and 5-day gaps, spring break puts a week-long hole mid-term, and 6.006's
   own syllabus says 2 lecture sessions/week — 20 lectures fill about 10 weeks of
   a 14.7-week term. Spreading 20 ordinals evenly across the whole term stretches
   a sequence that does not stretch.
2. *Interpolation is only as good as the completeness of the upload.* The range
   comes from the ordinals actually present. The live run below dated
   `MIT6_006S20_lec11.pdf` to the last day of term because 11 was the highest
   lecture uploaded. A student who uploads lectures 1–11 of 20 gets lecture 11
   placed weeks late, and nothing in the filename reveals the error.

The mitigating property, and the reason this is computed at all rather than not:
**interpolation is monotonic in the ordinal, so it never reorders anything.** The
dates drift; lecture 4 still sorts before lecture 5. Since the product's headline
is a chronological *ordering* with a first occurrence marked, order is the
load-bearing output and the absolute date is the displayed one. That asymmetry —
order reliable, date not — is exactly what `occurred_at_source` is for, and it is
pinned by a test.

**Decision — `inferred_filename` does not write `occurred_at`. It offers a
candidate.**

`date_course_from_filenames` writes only `filename_date`, the date a filename
states outright. An interpolated date is returned as a `DateCandidate` in the
outcome's `candidates`; the document stays undated in the database.

The reasoning is the two paragraphs above taken seriously. Extraction is
measured and reliable, ordering is reliable by construction, and the date is
neither — and it fails by *weeks*, not days, with nothing in the input revealing
the error. A student who uploads 11 of 20 lectures gets lecture 11 rendered as
mid-May with a straight face. A confidently-weeks-wrong date in a timeline is
worse than an honest blank, and it is worse in the specific way this product
cannot afford: it discredits the timeline rather than one row of it.

The candidate is still computed, and that is deliberate too. It becomes an
alternative the user accepts in one click rather than a date they must look up
and type, it preserves the disagreement signal slice 3 needs when a syllabus and
a filename claim different dates, and it means the work is not thrown away if
the measurement later says the interpolation is fine.

**What would reverse this: a measured date accuracy for `inferred_filename`, on
real course material whose real lecture dates are known.** Specifically not a
larger filename corpus — that measures extraction, which is already measured
(58/12/0 at the time, 70/0/0 once `quiz` and `review` were fitted) and is not the
thing in doubt.

Suggestions are computed per call and never stored. The interpolation range comes
from whichever ordinals are present, so one more upload changes every suggestion
in the course; a cached candidate would be stale the moment the next file lands,
and recomputing is correct by construction. Pinned by a test that uploads
`lec20.pdf` and watches lecture 9's suggestion move from the end of term to the
middle.

**Every branch that could go either way resolves to `None`.** `02-11-2020` is
refused whenever both readings are real dates, because February 11th and
November 2nd are equally consistent with the string and the convention depends on
where the person naming the file grew up; `13-02-2020` is read, since only one
ordering survives and nothing is being guessed. A missing year is taken from the
term only when the term contains exactly one such date. A lone ordinal returns
`None` from the arithmetic itself — `lowest == highest` is no range — rather than
through a special case someone must remember to write.

Ordinals are grouped by *kind* and interpolated within their own group. A course
holding twenty lectures and twelve recitations has two sequences sharing a term,
and pooling them would put recitation 12 beside lecture 20 at the end of the
semester.

**A hand-set date is never overwritten, including under `--overwrite`.** That
flag exists to re-run inference after fixing a course's term bounds. A person who
typed a date in outranks every heuristic here, so the flag deliberately cannot
reach `manual`.

**The first implementation of that check was broken, and the way it broke is the
second tooling lesson of this phase — the peer of slice 1's docstring finding.**
It compared with `is` against the `StrEnum`. `occurred_at_source` is
`Mapped[str | None]` over `String(32)`, so a row loaded from the database carries
a plain `str`, not the enum member; `document.occurred_at_source is
OccurredAtSource.MANUAL` was therefore **always false**, and hand-set dates were
being overwritten — the one thing the function documents at length that it must
never do. Caught by the test written for that rule, not by review, and not by the
type checker: the annotation says `str | None`, and `str is StrEnumMember` is a
legal expression no tool objects to.

Generalised: **an identity comparison against a `StrEnum` fails open whenever the
value came back from the ORM.** A column typed `str` is never the enum member,
however much the enum serves as its vocabulary. The failure is silent in the
worst direction — `is` returns `False`, the guarded branch is skipped, and the
protection quietly does not exist rather than raising and being noticed. Where
slice 1's lesson is about a check whose cheapest fix damages what it protects,
this one is about a check that reads correctly, type-checks, and does nothing.
Both are findable only by a test asserting the *protected behaviour* rather than
the mechanism. Use `==` for these columns; `is` is correct only for a value that
came from a caller's own enum, as in `redate_document`'s `source` parameter.

Verified live against the dev database, not only the test database: seven
documents ingested under real OCW filenames produced **1 dated, 5 suggested, 1
undated** — the single `filename_date` written, five interpolated candidates
offered across two independently-ranged kinds, and `scan.pdf` surfaced with a
reason. The scratch course was then deleted, which took the manual
three-statement transaction the missing `ON DELETE CASCADE` forces (chunks, runs,
documents, course) — friction confirmed as real, though a one-off cleanup script
is not the "first code path that deletes a `documents` row" that item waits for.
Suite is 113 tests, 40 of them new.

### Settled in slice 3 (syllabus dating, layout-independent half)

**The slice was deliberately split, and only half of it was built.**
`date_course_from_syllabus` takes a `Sequence[ScheduleEntry]` — `kind`,
`ordinal`, `occurred_on` — rather than a PDF. Everything downstream of that type
is the phase's actual subject matter: the join, the conflict policy, the funnel,
the honesty rules. Everything upstream is layout matching, and **no real syllabus
existed to build it against.** Writing a parser against an invented format and
then measuring it on that same invented format produces a number that looks like
evidence and is not — the same reasoning that kept `quiz` and `review` out of the
keyword map after slice 2's corpus was measured. The parser lands when two or
three real syllabi from different schools are in `test-data/`; one example means
building against one school's quirks.

**The join is on the ordinal, not on topic text.** A syllabus gives an ordered,
dated session list; a filename gives a session number. `lecture 7` to `lecture 7`
is exact, and ordinal extraction is already measured at 0 wrong. Matching a
syllabus topic against a document's prose would be a similarity score — a new
heuristic with a new unmeasured error rate, substituted for one that has been
measured. There is no version of that trade worth taking.

This is also what repairs slice 2's weak spot rather than working around it.
Interpolation had to guess where lecture 7 fell by spreading ordinals across the
term, which fails by weeks and is why those dates are offered rather than stored.
A syllabus states the date outright, so **the same ordinal that could only
produce a suggestion now produces a fact** — `parsed_syllabus`, written. The
enum's top value finally has a writer.

**Where the syllabus and the filename disagree, neither is stored.** Both come
back as `candidates` for a person to settle. Syllabi are published in advance and
classes get moved, so the schedule is not automatically right; a student's
filename is not automatically right either. There is no principled tiebreak
between two pieces of testimony, and picking one silently is *worse* here than
having no date at all — the disagreement is evidence that one of the two sources
is unreliable for this whole course, and resolving it quietly throws that
evidence away. Agreement is not a conflict: two sources saying the same thing is
the good case and writes `parsed_syllabus`.

That conflict path is why `DatingOutcome.suggestion` became
`candidates: tuple[DateCandidate, ...]`, carrying its source with each date. One
candidate is an offer, two is a disagreement. A conflict described only in
`reason` prose is not something a UI can turn into a one-click answer.

**No interpolation fallback when the syllabus is silent about an ordinal.**
`lec9.pdf` against a schedule with no lecture 9 stays undated with a reason, and
does not quietly fall back to spreading ordinals across the term. A syllabus
being present does not make that guess any better than slice 2 measured it to be.
A filename that states a date outright is still used where the syllabus says
nothing — a guest lecture keeps its `filename_date`.

**A schedule that dates one session twice, differently, is rejected outright**
with `ServiceError` before anything is written, because whichever row won would
depend on iteration order, and that is not a decision anyone made. A repeated
*identical* row is accepted — only a contradiction is an error. An out-of-term
schedule date is refused by the funnel and reported as an undated outcome, not
raised, so one bad row cannot take the other nineteen documents down with it.

Both dating paths share the eligibility rules, including `manual` outranking
everything under `--overwrite`. A `parsed_syllabus` date *does* replace an
`inferred_filename` one under `--overwrite` — that is the upgrade path, a guess
becoming a fact, with `occurred_at_source` moving with it.

12 new tests; suite is 125. The conflict test was mutation-checked — with the
disagreement branch disabled, the syllabus date is silently stored and the test
fails — because a test that has never failed is a claim, not a guard. Same
discipline as `verify`'s fault injection in Phase 1 and the two tests proving
slice 1's AST guard can fail.

### Settled in slice 4 (the syllabus parser)

`app/services/syllabus_schedule.py` — pure, text pages in and `ScheduleEntry`
rows out, no PDF and no session, for slice 2's reason. `ScheduleEntry` moved here
from `dating.py`, since the module that produces a type should own it;
`dating.py` gains `parse_course_syllabus`, two lines of orchestration that fetch
the course for its term bounds, and `date-course --syllabus <pdf>` makes the
slice runnable.

**Two worked examples, one positive and one negative. Not a hit rate, and it must
not drift into being written up as one.** Slice 2's 58/12/0 was a measurement
because 70 labelled filenames is a sample. Two syllabi are not. What they do
establish is the claim the parser is built on: the layout it reads exists in the
wild, and so does one it cannot. That is the evidence slice 3 was waiting for,
and it is why nothing was built against an invented format.

Both fixtures are committed as whole extracted documents rather than trimmed to
the schedule page, so the parser has to find the table among everything else, and
a test re-extracts the real PDFs and compares. A fixture nobody re-derives from
its source drifts into being an invention.

**One layout is recognised; everything else is reported as unrecognised.** A
*linear* schedule — one session per row, ordinal and date on the same line —
survives `extract_text` because the row *is* the line. A *calendar grid* does
not: York's flattens into a run of dates (`12 13 14 15 16`) and a run of labels
(`Lecture 3 Lecture 4 Tutorial 2`), three labels against five columns, with the
mapping between them left behind in the cell geometry. Nothing short of re-reading
the PDF's word coordinates recovers it, so the parser says so and dates nothing.
The refusal is the deliverable: a plausible guess at a grid would be wrong by
days, for every lecture in the course, with nothing in the output revealing it.

**The acceptance rule is one sentence and does three jobs: a schedule is the
longest run of consecutive rows whose ordinals and dates both strictly
increase.** It finds the table among the rest of the document, it separates a
syllabus holding more than one dated list (the longer wins; they are not merged
into a series counting 1, 2, 1, 2), and it is the accept/reject test. A grid
produces no such run because the lines with dates have no ordinal and the lines
with ordinals have no date. Below three rows there is no schedule — two lines
beginning with a number and a date occur in ordinary prose.

Runs are of *rows*, not lines, which is why Waterloo's wrapped topics need no
handling at all. Its longer topics straddle their row — the first line of the
topic sits above `(9) Nov 08` and the rest below it — and none of it matters,
because the join is on the ordinal and topic text is never read. Layout that
would defeat a topic-matching parser is invisible to this one. That is slice 3's
join decision paying out a second time.

**Ordinals are read, never counted — and this is the design choice most likely to
be undone by someone who does not know why it is there.** The number comes from
the row's own text, so Waterloo's two unnumbered `(-)` rows (reading week, and a
spare week after the last topic) can be dropped without disturbing anything.
Numbering rows by position instead is the obvious implementation, produces an
*identical* result on any schedule without gaps, and on this one gives reading
week the number 6 — pushing weeks 6 through 12 back by seven days each, so every
date from mid-October to the end of term is wrong. Uniformly, plausibly, and
invisibly: the output looks exactly as orderly as the correct one. This phase's
failure mode in miniature.

**The first test of that property could not fail, which is the slice's tooling
lesson.** Asserting against the real fixture looked like the strongest possible
test — real syllabus, real reading week — and it was worthless for this, because
ECE 606's weeks run 1..12 with no gaps and positional numbering yields the same
twelve numbers. Confirmed by mutation: the positional version passed. The guard
is now a synthetic schedule numbered 1, 2, 4, 5, where the two implementations
differ, plus a real-data test that reading week consumes no number. Six tests
fail under the mutation now.

Generalised, and a peer of the two lessons above it. The mechanism, which is the
part worth keeping:

**A fixture guards a property only if the wrong implementation would produce a
different answer on that fixture.** Nothing else about it matters — not its
provenance, not its size, not how faithfully it represents production. A test
over real data that both implementations satisfy is not a weak guard, it is not a
guard at all, and it fails silently in the direction that never gets
investigated: green.

**Regular material is precisely where an off-by-one has nothing to catch it.**
Real timetables number 1..12 without gaps, real invoices are sequential, real
pagination starts at 1 — regularity is what makes real data *look* like the
strongest available input, and it is the exact structural property that collapses
the difference between counting and reading. The more typical the fixture, the
more likely the two implementations agree on it.

**And realism reads as rigor, which is why it goes unexamined.** "Tested against
a real syllabus with a real reading week" is a sentence that ends a review. It
sounds like the strongest possible claim, so nobody asks the only question that
matters, which is what the wrong version would have returned here. That is the
same shape as slice 1's finding — the cheap repair that looks like diligence — and
slice 2's — the comparison that reads correctly and does nothing.

**The lesson is not "add synthetic data too."** Synthetic cases are how this
particular property got covered, but a synthetic fixture chosen without asking the
discriminating question is just as blind. The discipline is the question: *what
does the plausible wrong implementation return on this input?* If the answer is
"the same thing", the test is documentation, and something else has to carry the
guard.

**A schedule that never says what it numbers is refused, not assumed to be
lectures.** Waterloo's rows are bare `(1)`, `(2)`; the unit comes from the `Week
of` column header above them, found by searching a short window of lines up from
the first row. The unit decides which documents can join at all, so guessing it
wrong dates the whole course from the wrong row, and `lecture` is the guess most
likely to look right while being wrong — weekly schedules are common, and a week
is not a lecture. Rows that name their own unit (`Lecture 3 Sep 20`) win over the
header, and rows that disagree with each other are refused rather than resolved
by whichever a set yields first.

The date must follow the ordinal *immediately* — `read_leading_date` is anchored
where `read_explicit_date` searches. `(3) Properties of algorithms, moved from
Sep 20` would otherwise date session 3 to the one day the sentence rules out.

**`read_leading_date` and `kind_for` live in `filename_dates.py`, not here**,
despite neither being filename-shaped. Both sides of the join need one session
vocabulary and one month table, and a second copy of either fails in the worst
available way: `week` against `weeks` joins nothing and reads as a syllabus that
mentions no sessions, and a divergent month table yields a wrong date rather than
a crash.

**Decision — a weekly schedule dates weeks, and is never converted into
lectures.**

Waterloo's schedule is headed `Week of`, so `(3) Sep 20` is the week beginning
September 20th. The parser emits `kind="week"`, and the join then misses for
`lecture-07.pdf` and hits exactly for `week-03-notes.pdf`. A weekly schedule is
not discarded — it dates what it actually numbers.

A course with two lectures a week has lectures 5 and 6 inside week 3, so the join
is not 1:1, and closing that gap needs a lectures-per-week figure that appears in
neither the syllabus nor the filenames. Deriving it by dividing uploaded lectures
by weeks assumes the student uploaded every lecture — the assumption slice 2
measured going wrong by weeks.

**The decisive objection is the column, not the arithmetic.** A date reached that
way would be stored with `occurred_at_source = parsed_syllabus`, which means *the
syllabus stated this date*, when the syllabus stated no such thing. That is a
false claim in the one field this phase exists to keep honest, and it is worse
than slice 2's interpolation, which at least carried a provenance value admitting
it was a guess. A wrong date is recoverable; a wrong date wearing the strongest
provenance the enum has is not, because nothing downstream has any reason to
doubt it.

The strongest counter-argument, taken seriously and rejected: a week *does* bound
a lecture to seven days, far tighter than interpolation's weeks of drift, so
offering it as a candidate looks nearly free. It is not — knowing *which* week
requires the same missing figure, so the bound is only ever as good as the guess
that selects it, and nothing is gained over saying nothing.

**Reversal conditions, stated as conditions rather than a phase** — the same
correction made to the `ON DELETE CASCADE` item, for the same reason: a phase
number comes due whether or not the thing it was really about has happened.
Either would reverse this:

1. **A lectures-per-week on `courses`, entered by a person.** Week-to-lecture then
   becomes arithmetic over stated facts, and `parsed_syllabus` stays true.
2. **Filenames that carry the weekday**, which resolve a lecture to a day within
   its week without any per-course figure.

Specifically not an inference of that figure from the files present, which is the
guess this rejects.

Reported honestly to the user instead: the outcome's reason names the granularity
mismatch rather than saying `the syllabus has no lecture 7`, which would send
someone hunting for a row that was never supposed to exist. Both readings leave
the document undated, which is exactly why the wrong wording would go unnoticed,
so it has its own test.

Three guards were mutation-checked, since a test that has never failed is a
claim: positional numbering (6 failures), a searching rather than anchored date
(1), and defaulting the unit to `lecture` (1).

16 new parser tests and 3 new dating tests; **suite is 144, all passing**,
including slice 1's AST guard and slice 2's 58/12/0 corpus, both unchanged.

**Deferred, with reasons:**

- **`tutorial` is not in the session-kind vocabulary.** Found while writing these
  tests — York schedules tutorials and `read_ordinal` would not recognise
  `tutorial-3.pdf`. Adding keywords after seeing the material is what slice 2
  declined to do with `quiz` and `review`, and the same applies: it belongs to a
  measurement, not to this slice. **Still deferred after `quiz` and `review` were
  added on 2026-08-15**, and the difference is the point: those two were fitted to
  rows in a labelled corpus that were 100% of its misses, so the cost of the
  fitting is bounded and written down. `tutorial` has no corpus behind it at all —
  no York filenames were ever collected, only a York syllabus — so adding it would
  be a guess with nothing to score. The trigger is real filenames from a course
  that runs tutorials.
- **Word-coordinate extraction for calendar grids.** `pdfplumber` exposes the
  geometry the flattened text loses, so a grid is not unrecoverable in principle
  — only unrecoverable from the text, which is what this parser reads. It is a
  different parser, and it needs more than one grid syllabus to build against.
- **More than one schedule table in a document** is handled only by longest-run.
  A syllabus with two genuine session series would need to emit both, and none
  has been seen.

### Settled in slice 5 (the dating UI, and the frontend's first test runner)

**The conflict branch was traced through the data flow before any UI was built,
and it turned out to be unreachable from a read request.** Slice 3 returns two
`candidates` when the syllabus and a filename disagree; slice 5's job was to
render that. It cannot: candidates are computed and never stored, a GET can
recompute the filename half from `storage_key` but the syllabus half only exists
inside `date_course_from_syllabus`'s arguments, and slice 3's rule stores
*neither* date in a conflict, so the disagreement leaves no trace in the database
either. Filed as a Deferred entry with both schema options rather than absorbed
into this slice to unblock one branch — it is a schema decision, and one of the
two options breaks the ten-table plan.

Worth naming as a pattern: the branch existed, was tested, and was described in
three documents, and nothing about it was wrong. What was missing was one link in
a chain nobody had walked end to end. **A feature that is correct at every step
can still be unreachable, and prose about behaviour will not reveal that** —
which is the same lesson as the `queued` run row Phase 2 claimed and did not
write, arriving from the other direction.

**The write half and the read half are separate functions, not one function with
a flag.** `plan_dates_from_filenames` decides every date and writes nothing;
`date_course_from_filenames` is now that call plus a write loop. A `dry_run=True`
on the dater would have been fewer lines and is the version to avoid: this module
exists to funnel writes through one place, and a parameter that switches writing
off is a second, invisible mode of that funnel, guarded by a caller passing the
right argument. The guarantee wanted here is "opening a page cannot date a
course", and the form of it that holds is that the function the route calls
contains no write at all. Mutation-checked — a `redate_document` added to the
planner fails both the service test and the route test.

The corollary is `DatePlan` being a distinct type from `DatingOutcome`.
`occurred_on` on an outcome means a date **was** written; on a plan it means one
**would** be. One type serving both readings is how a read request ends up
rendering a date nothing stored.

**Register rules for the UI, which is what this slice is actually about:**

- **The date column shows stored dates only.** Nothing else may render there —
  not greyed out, not in italics, not with a question mark. Every one of those
  puts a guess where a fact goes and leaves the reader to notice the styling.
- **A button is not a value.** Candidates render below the row as verbs (`Use Feb
  11, 2020, from the filename`), so they read as something to do rather than
  something that is true.
- **Two candidates render side by side and identically weighted.** Not stacked:
  vertical order reads as ranking and a first option reads as a default. No
  `variant="default"`, no "recommended", no confidence figure. Surfacing a
  disagreement and then visually answering it is worse than not surfacing it.
- **Accepting a candidate writes `manual`.** The enum answers who is responsible
  for the date, and after a click that is a person, not the heuristic that put
  the option in front of them. The PATCH route takes no `source` field precisely
  so this cannot be laundered.
- **Undated is content, not an empty cell.** Every undated row carries the
  backend's reason **verbatim** and a date input, so the state is always
  actionable. The count above the list comes from the server, not from counting
  the rows client-side, so it cannot disagree with them.

**No course-level banner was built.** The plan had one for `unrecognized schedule
format`, on the reasoning that it is a course fact rather than a document fact.
That reasoning still holds and there is nothing to put in it: the syllabus
parser is CLI-only and its refusal is not persisted, by the same deferral above.
Building the banner now would mean building a component with no data source.

`describeDate` totals over 0, 1 and 2+ candidates even though 2 is unreachable
today. That is not speculative abstraction — the function has to be total over
the list regardless, and the version that handles one candidate and lets the rest
fall through silently drops the second one on the day persistence arrives, which
is the day it matters most.

**Vitest, no jsdom, no testing-library.** Everything worth pinning in both
features is a pure function, and both were written that way so the decisions
could be tested without rendering: `describe` turns two counts into a stage,
`describeDate` turns a document into one of four states. Installing a DOM
environment no test needs is a dependency added for later. `@vitejs/plugin-react`
was also skipped — it conflicts on peers with this Vite and Vitest transforms
`.tsx` from the tsconfig's `jsx: react-jsx` without it.

**Phase 2's carried-over `describe()` assertions landed here**, as that item
said they would once a test runner was justified. The rule they protect is that
**a ratio exists only when two real counts do** — the bar renders from
`stage.ratio`, so anything that fills it during extraction produces a progress
bar advancing on invented data, which is what keeping `Progress` out of
`components/ui/` was meant to prevent, arriving from the other direction. One
test explicitly moves `attempts`, `run_status` and `stale` and asserts the ratio
does not follow, because those are what a plausible fake progress number would be
built from. Mutation-checked, along with `describeDate`'s two-candidate branch.

**`formatDay` parses the day, never the instant.** Dates are stored as midnight
UTC, so formatting through the browser's zone shows the previous day to everyone
west of Greenwich — an off-by-one that reproduces only in some timezones and
reads as bad data rather than a bug.

The courses API moved from `features/upload/api/` to `src/lib/courses.ts`. Two
features need it and features do not import from each other (invariant 6);
copying the type would give one backend schema two mirrors that drift.

Verified end to end against the running app on 2026-08-13, not just in tests: all
three undated shapes render with their reasons (`no date or lecture number in the
filename`, `lecture 1 of 1..11; interpolated, so offered rather than stored`, `the
filename states 2020-02-11; nothing has stored it yet`), and clicking the
filename-date offer stored `2020-02-11` as `manual` and moved the count from 6 of
6 to 5 of 6.

Backend suite is **150**, frontend **17**, both passing; `next build` and
`eslint` clean.

**Deferred, with reasons:**

- ~~**Nothing links to `/documents`.**~~ **Closed the same day, and the entry was
  wrong when filed.** It claimed `/upload` was unlinked too and that the app had
  had no navigation since Phase 0; `layout.tsx` has had a nav with `/` and
  `/upload` in it the whole time, and only the new route was missing from it.
  Worth leaving visible rather than deleting: the claim was written from memory
  of the pages instead of from the layout, and it made a one-line omission sound
  like a structural gap — the same failure as treating a prose claim about
  behaviour as fact, in the direction of overstating the work left.
- **No sorting or filtering by date.** The list is in upload order, and undated
  rows sit among the dated ones rather than being collected at the bottom,
  because a list that buries its gaps is how "surfaced, never silently defaulted"
  turns back into a silent default. Sorting belongs with the timeline in Phase 4.
- **No dating runs from the UI** — no "date this course" button and no syllabus
  upload. Both write across a whole course at once, and a one-click batch write
  is not something to hand a UI in the slice that first renders the results.

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

- **Render's free Key Value plan has no persistence — settled for Phase 2, still
  open for production.** A restart or eviction drops everything in it. For Phase 0
  that is irrelevant, because the only thing touching Redis is a health ping with
  no state to lose. Once arq holds a job queue there, a restart mid-document
  silently loses enqueued work, and the honest fix is a paid plan rather than
  application-level retry logic.

  **Phase 2 works around it rather than solving it**, by making Postgres the
  record of intent and Redis only the dispatch mechanism — see Phase 2's
  constraints. That makes lost work *visible* as a run row stuck at `queued`; it
  does not make it *not lost*. The paid plan is still the fix, and the trigger is
  the first time this runs unattended for anything that matters.

  *(This item previously read "before Phase 3 puts arq on it". The queue is
  Phase 2 — the reference was one phase stale, which is how a deferred item
  quietly gets skipped past its own deadline.)* Whatever instance ends up serving arq, it has to
  sit in the web service's region — see Phase 0's constraints for why, and for
  what the failure looks like when it doesn't.

- **Render's free web service spins down when idle, so `/health/ready` is not an
  uptime check there.** Render's own healthcheck does not keep a free instance
  awake; an external pinger would, at the cost of the monthly instance-hour
  allowance. Treat the route as a deploy gate and a dependency probe — which is
  what it was built for — not as monitoring.

- **A freshly deployed environment is unusable through its own UI.** There are no
  courses, and no way to create one except `python -m app.cli create-course` run
  against that environment's database. Every path the app offers a person —
  the upload page, and everything Phases 4-6 build on top of it — requires a
  course to exist first. So the deployed app does nothing at all until someone
  holding the production connection string intervenes from a shell.

  Found on 2026-08-10, bootstrapping the first production upload: the upload page
  correctly reported that there were no courses, which is the designed behaviour
  and also a dead end. Both halves of that are true at once, and that is what
  makes it easy to leave unfixed.

  **CLI-only course creation is deliberate and is not the thing to undo.** Term
  bounds are `NOT NULL` because Phase 3 dates lectures by interpolating within
  them, so a course with wrong or guessed bounds silently produces wrong dates
  rather than an error — that is why they are entered by hand, deliberately, in
  a place that is hard to fill in carelessly. The gap is that there is no
  *second* path, not that the first one is wrong.

  **This collides with Phase 7 and is not covered by it.** Phase 7 seeds a demo
  course from MIT OCW 6.006 for a logged-out visitor to browse, which fixes the
  arriving stranger's *first* screen and nothing after it. The moment that
  stranger signs up and wants their own material, they are back in the same dead
  end, holding a PDF and a picker with nothing in it. Auth is what turns this
  from an operator inconvenience into a broken product: today there is one user
  and he owns the database.

  Worth naming because it does not read as a design decision from outside. To
  anyone who did not build this, an upload page that cannot accept an upload is
  a bug, and the correct behaviour on display — an honest empty state instead of
  an empty picker — makes it look more finished and therefore more broken.

  The fix is a course-creation path in the product, whatever shape survives the
  constraint above: a form that requires start and end dates rather than
  defaulting them, or Phase 3's syllabus parsing running first and proposing
  bounds a person confirms. Sequenced with Phase 7, since that is where the
  second user appears; the Phase 3 route may make it nearly free by then.

- **A parsed schedule is not persisted anywhere, so a syllabus/filename conflict
  cannot survive the CLI invocation that produced it.** Slice 3's rule is that
  where the two disagree, *neither* date is stored and both come back as
  `candidates`. That rule is sound and it has an unnoticed consequence: the
  disagreement leaves no trace in the database either. `documents.occurred_at`
  stays null, which is correct, and nothing anywhere records *why*. Found in
  slice 5 while tracing the data flow behind the candidates UI, before building
  it — a read request can recompute the filename half from `storage_key`, but the
  syllabus half only exists inside `date_course_from_syllabus`'s arguments. So the
  two-candidate branch is real, reachable from the CLI, and unreachable from a
  GET.

  Two shapes, and this is a schema decision that deserves its own consideration
  rather than being absorbed into whichever slice it happens to block:

  - **`courses.schedule` as JSONB.** No new table, no migration beyond a column,
    and the schedule is genuinely one document — it is parsed as a unit, replaced
    as a unit, and never joined against. The cost is that it is opaque to SQL:
    "which courses have a session on this date" becomes a scan, and there is no
    foreign key from an entry to anything.
  - **A `course_schedule_entries` table.** Rows with `user_id`, `course_id`,
    `kind`, `ordinal`, `occurred_on`, and the usual `created_at`. Queryable,
    constrainable — a unique index on `(course_id, kind, ordinal)` would enforce
    at the database what `date_course_from_syllabus` currently checks in Python
    before writing. **It also makes eleven tables, and CLAUDE.md says ten.** That
    is not a veto, but the schema is stated as a closed set with a rationale per
    column, so adding to it is a decision to make deliberately and write down,
    not a side effect.

  **Trigger: the first time a conflict needs to survive past the CLI invocation
  that produced it.** Not a phase — the same correction made to the cascade
  entry. Today the conflict is printed and the operator acts on it immediately,
  which is a complete story for one user with a terminal. It stops being one the
  moment the person resolving the disagreement is not the person who ran the
  command.
