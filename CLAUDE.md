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

**Phase 4 — retrieval + timeline.** The demo. Vector search over `chunks`, an
HNSW index, `POST /search`, and a chronological timeline with the first
occurrence badged and every hit deep-linking to its source page. Read ROADMAP's
Phase 4 section first.

**The dating constraint shapes the whole UI, and the Phase 3 audit's framing of
it needed correcting: coverage is weak, accuracy is not.** Unattended dating
writes zero dates *by design* — `inferred_filename` has no writer since slice 5,
so a date that is stored was either stated by a syllabus, stated by a filename,
or set by a person, and all three are worth trusting. The consequences:
**the timeline is an ordering, not a scale** — no proportional time axis, no
month gaps drawn to size, because interpolated spacing is exactly the precision
we do not have; and **the first-occurrence badge is suppressed whenever any
undated document also matches**, since "first" is a claim about the whole corpus
and an undated match could precede it. Undated matches are shown, grouped, never
silently dropped.

**Retrieval quality is the phase's real question and a passing test does not
answer it.** Slice 4's eval reports a four-way tally — `first-correct` /
`first-wrong` / `not-found` / `unrankable` — plus recall@k and a keyword
baseline, against 20 real MIT 6.006 lectures whose ground truth is MIT's own
topic titles. **The numbers and the baseline get reported before any verdict on
whether retrieval is good**, same as 58/12/0. If vector search does not beat the
keyword baseline on this corpus, that gets said plainly rather than reframed —
it is the more useful result.

Settled by decision, not to be re-opened: hits group **per document**, not per
chunk; a tie for earliest badges **both, labelled earliest, with the count**;
the PDF opens in the **native viewer at the right page** and **the passage
highlight is ours, not in-PDF** (ROADMAP's wording could be read as promising
otherwise and should not be); the file is served by our own route mirroring
`core/storage.py`; and course scoping is **optional, cross-course by default** —
longitudinal across a degree is the product. The corpus is dated by hand as
`manual`, because zero dated documents would leave the headline claim unmeasured.

Slice 1 is done: the eval corpus is in. `backend/evals/` holds a **pinned**
manifest (URL + `sha256` for 20 lecture PDFs), `lecture_topics.tsv` ground truth,
and a stdlib-only fetcher that refuses bytes whose hash does not match; the PDFs
themselves are gitignored. **20 documents `ready`, 216 chunks, 216 embedded,
`verify` clean on all 20.** Real material immediately found a Phase 1 bug —
lecture 16 carries twelve U+0000 characters that Postgres `text` cannot store —
fixed by `normalise_page_text`, the "one named function called by both" that
`extraction.py`'s docstring had already reserved. **It substitutes U+0000 →
U+FFFD rather than stripping**, because same-length is what keeps every stored
`char_start`/`char_end` valid; a strip would pass a test that only checked the
character was gone and silently shift every offset after it. Full reasoning in
ROADMAP's "Settled in slice 1".

Slice 2 landed search with no index: `POST /search`, `services/search.py`,
`repositories/search.py`, a `search` CLI command, and 9 tests whose three
load-bearing queries are mutation-checked (sort direction, owner filter, null
embeddings). **The pre-index measurement over 216 embedded chunks in 20
documents: the OpenAI round trip is 254 ms median, the client-observed query is
45 ms, and the query's server-side execution is 1.16 ms.** The gap is not the
scan — it is pgvector's text protocol serialising 1536 full-precision floats into
a 34 KB literal. The same query with short floats runs in 1.84 ms instead of
43.99 ms. **Consequence for slice 3: an HNSW index can only attack the 1 ms, so
"the index changed nothing measurable" is a likely and acceptable result and must
be reported as one.** The binary codec that would remove the 42 ms is measured
and deliberately unbuilt; see ROADMAP's "Settled in slice 2".

Phase 3 is closed, tagged `phase-3-complete`. Its scope was syllabus parsing,
filename inference, a manual override endpoint, and a UI showing each document's
date and where that date came from. Documents that cannot be dated are surfaced,
never silently defaulted. The load-bearing constraint was invariant 4 made
concrete: **exactly one service function, `redate_document`, writes
`documents.occurred_at`**, and all three dating paths route through it. It exists
before there is anything to cascade to, because Phase 5 adds a write that must
accompany every date change and no constraint can force it.

One item is still carried forward: `documents` has no `ON DELETE CASCADE`, and
the trigger for adding it is the condition **"first code path that deletes a
`documents` row"** — not a phase. Phase 3's other carried item is done: the
frontend test runner exists, and Phase 2's `describe()` assertions landed with
it in slice 5.

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
undated, 0 wrong.** The pin now reads **70 / 0 / 0**, because on 2026-08-15
`quiz`, `review` and the `q` letter ordinal were added — the twelve undated rows
were 100% of the misses, so **those twelve are fitted, not measured**, and a rule
scored on the rows that produced it reaches 70 by construction. **0 wrong is the
figure that survived and the only one to cite**: clearing the `6`/`006`/`20`
decoys is a parser property, not a vocabulary one. That measures ordinal
*extraction* against decoy numbers, not
date accuracy; those filenames carry no dates, so `inferred_filename`'s date
accuracy is **unmeasured and unclaimed**. Interpolation is expected to drift —
real timetables are unevenly spaced, and the range depends on which files the
student happened to upload — but it is **monotonic, so it never reorders**. Order
is reliable, the date is not; that asymmetry is the whole point of
`occurred_at_source`. Every ambiguous branch resolves to undated, and a hand-set
date is never overwritten, including under `--overwrite`.

Slice 3 landed **half** of syllabus dating, on purpose. `date_course_from_syllabus`
takes a `Sequence[ScheduleEntry]`, not a PDF — everything downstream of that type
is the phase's real subject and is testable today; everything upstream is layout
matching, and **no real syllabus existed to build against.** Writing a parser
against an invented format and measuring it on that same invention produces a
number that looks like evidence and isn't. The parser waits on two or three real
syllabi from different schools in `test-data/`. **The join is on the ordinal, not
on topic text**, because ordinal extraction is already measured at 0 wrong and
topic similarity would swap a measured error for an unmeasured one. This repairs
slice 2's weak spot: the same ordinal that could only be interpolated now resolves
to a stated date, so `parsed_syllabus` finally has a writer. **Where the syllabus
and the filename disagree, neither is stored** — both come back as `candidates`
(which replaced `suggestion`, since one candidate is an offer and two is a
disagreement), because there is no principled tiebreak between two pieces of
testimony and the disagreement is itself evidence about the whole course. There is
**no interpolation fallback** when the syllabus lacks an ordinal. A schedule that
dates one session twice, differently, is rejected before anything is written; a
repeated identical row is fine. The conflict test was **mutation-checked** — a
test that has never failed is a claim, not a guard.

Slice 4 landed the parser: `services/syllabus_schedule.py`, pure over extracted
page text, plus `parse_course_syllabus` and `date-course --syllabus`.
`ScheduleEntry` moved into that module. **A schedule is the longest run of
consecutive rows whose ordinals and dates both strictly increase** — one rule that
finds the table, separates a second dated list, and decides accept/reject. It
reads Waterloo's ECE 606 (12 weeks, reading week skipped) and reports York's
EECS 3101 calendar grid as unrecognized, because the date-to-lecture mapping lived
in cell geometry that `extract_text()` discards. **Two worked examples, one
positive and one negative — not a hit rate, and it must not be written up as
one.** Wrapped rows need no handling at all: runs are of rows, not lines, and
topic text is never read.

**Ordinals are read, never counted.** Numbering rows by position is the obvious
implementation, gives an identical result on any gap-free schedule, and hands
reading week the number 6 — pushing every date after mid-October back a week,
uniformly and invisibly. The first test of this **could not fail** (ECE 606 runs
1..12, so both implementations agree on it); the guard is now a synthetic
1, 2, 4, 5 schedule. Generalised in ROADMAP as a peer of the other two
silent-failure lessons, and the mechanism is the part that matters: **a fixture
guards a property only if the wrong implementation would answer differently on
that fixture.** Regular material — 1..12, no gaps — is precisely where an
off-by-one has nothing to catch it, and realism reads as rigor, so the claim
"tested against a real syllabus" ends a review instead of starting one. **Not a
rule to prefer synthetic data**; the rule is to ask what the plausible wrong
implementation returns on this input, and if it is the same thing, the test is
documentation.

**A weekly schedule dates weeks and is never converted into lectures.** `(3) Sep
20` under a `Week of` header is a week; week 3 of a twice-weekly course holds
lectures 5 and 6, and closing that gap needs a lectures-per-week figure stated
nowhere. The decisive objection is the column, not the arithmetic: such a date
would be stored as `parsed_syllabus` — *the syllabus stated this* — when it stated
nothing of the kind, which is worse than slice 2's interpolation because it wears
the strongest provenance the enum has. Week-numbered files still join exactly.
**Reversal conditions, not a phase:** a lectures-per-week on `courses` entered by
a person, or filenames carrying weekdays. Not an inference of that figure from the
files present. A schedule that never says what it numbers is **refused, not
assumed to be lectures**. Suite is 144, all passing.

Slice 5 landed the dating UI (`GET /documents?course_id=`, `features/documents/`,
`/documents`) and the frontend's first test runner. Building it surfaced that
**slice 3's two-candidate conflict is unreachable from a read request**: nothing
persists a parsed schedule, so a GET can recompute the filename half and never
the syllabus half, and slice 3 stores neither date in a conflict so the database
holds no trace either. Filed as a Deferred ROADMAP entry (`courses.schedule` as
JSONB versus a `course_schedule_entries` table, which would break the ten-table
plan), triggered by the first time a conflict must survive the CLI invocation
that produced it. **A feature correct at every step can still be unreachable, and
prose about behaviour will not reveal it** — walk the data flow.

Two rules the code depends on and cannot enforce. **The read half is a separate
function, `plan_dates_from_filenames`, not a `dry_run` flag** — this module
funnels writes, and a flag that switches writing off is a second invisible mode
of the funnel; the guarantee that holds is that the function the route calls
contains no write at all. And **the date column shows stored dates only**:
candidates render below the row as verbs on buttons, two of them side by side and
identically weighted with no default and no confidence, and accepting one writes
`manual`, because the enum answers who is responsible and after a click that is a
person. Undated rows carry the backend's reason verbatim plus a date input.

Phase 2's carried-over `describe()` assertions landed in the same slice, as that
item required. Vitest only — no jsdom, no testing-library, no
`@vitejs/plugin-react` — because everything worth pinning in both features is a
pure function. No course-level banner was built: the syllabus refusal it would
show is not persisted, so it would be a component with no data source.

**Decision, load-bearing for the rest of the phase: `inferred_filename` does not
write `occurred_at`.** `date_course_from_filenames` stores only `filename_date`
— a date the filename states — and returns an interpolated date as a
`suggestion` the user accepts in one click. Interpolation fails by *weeks*, not
days, with nothing in the input revealing it, and a confidently-wrong date
discredits the whole timeline rather than one row. The candidate is still
computed, so slice 3 keeps its disagreement signal. **This reverses only on a
measured date accuracy against real material with real lecture dates** — not on
a larger filename corpus, which would re-measure extraction. Suggestions are
never cached: one more upload changes the interpolation range for the whole
course.

Two silent-failure lessons are recorded in ROADMAP as peers: slice 1's **guard
whose cheapest fix damages what it protects**, and slice 2's **`is` against a
`StrEnum` fails open on any ORM-loaded value** — always false, type-checks
clean, protection silently absent. Use `==` for those columns.

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
