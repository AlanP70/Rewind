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

**Carried in from Phase 2, slice 2 — `documents` has no `ON DELETE CASCADE`**

`fk_chunks_document_id_user_id` and the equivalent on `processing_runs` reference
the `(id, user_id)` pair without a cascade, so deleting a document is a
three-statement transaction — chunks, then runs, then the document — and a plain
`DELETE FROM documents` fails on a foreign key instead. Found by hand when a
stale Phase 1 row had to be removed after the `storage_key` rename.

Deliberately not fixed in Phase 2, which has no delete path: a migration for a
feature that does not exist is speculative. **It is recorded here because Phase 3
is where it stops being free.** Re-dating and re-processing touch documents whose
children may need rebuilding, and the first code that deletes one will either
discover this as a foreign-key error or, worse, quietly leave chunks behind
pointing at a document that is gone. Decide the cascade deliberately when
`redate_document` lands — including whether `processing_runs` *should* cascade at
all, given that run history outliving its document is arguably the point of
keeping it.

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
