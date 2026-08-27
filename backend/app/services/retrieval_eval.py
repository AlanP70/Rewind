"""Scoring the retrieval eval. Pure functions over hits -- no session, no network.

Pure so that the scoring can be tested against hand-built hit lists where the
right answer is known by construction. A scorer that can only be exercised by
running the real thing is a scorer whose bugs show up as retrieval results.

Both rankers are scored by this one module, deliberately: the comparison between
vector search and the keyword baseline is only meaningful if a scoring mistake
hits both of them equally.

What the numbers here do *not* measure, stated once so it is not inferred later:
one course, one subject, one institution, 20 documents and 16 questions. At 16
questions **one question is 6.25 points**, so a gap of one or two questions
between the two rankers is noise and is reported as no measurable difference.
That threshold is fixed here, before the run, because a margin chosen after
seeing the numbers is not a threshold.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.schemas.search import SearchHit
from app.services.filename_dates import read_ordinal
from app.services.timeline import build_timeline

# Scored at 20 hits, but fetched deeper. The extra 20 exist only for the page-1
# counterfactual: dropping header chunks out of a 20-hit list and re-scoring the
# remainder would compare 20 hits against 14, which understates the effect and
# reads as "boilerplate barely matters".
SCORE_AT = 20
FETCH = 40


class Outcome(StrEnum):
    FIRST_CORRECT = "first-correct"
    FIRST_WRONG = "first-wrong"
    NOT_FOUND = "not-found"
    UNRANKABLE = "unrankable"


class Phrasing(StrEnum):
    LITERAL = "literal"
    PARAPHRASE = "paraphrase"


@dataclass(frozen=True)
class Question:
    id: str
    pair: str
    kind: Phrasing
    query: str

    # The lowest-numbered lecture whose MIT title covers the topic. What the
    # four-way tally is scored against.
    expected_first: int

    # Every lecture whose MIT title covers the topic. What recall is scored
    # against. A superset of `{expected_first}`.
    relevant: frozenset[int]


@dataclass(frozen=True)
class QuestionResult:
    question: Question
    outcome: Outcome

    # Which lectures the timeline badged as earliest. One element normally; two
    # is a date tie, which is why the outcome is then `unrankable`.
    badged: tuple[int, ...]

    # Set only for `unrankable`, which has three quite different causes and is
    # useless as a single bucket.
    reason: str | None

    recall_at_10: float
    recall_at_20: float

    page1_in_top3: bool

    # The same question scored again with every page-1 chunk removed. Compared
    # against `outcome` this answers the question the phase actually asked:
    # not "is boilerplate present" but "did boilerplate cost an answer".
    outcome_without_page1: Outcome


def load_questions(path: Path) -> list[Question]:
    """Read `evals/questions.tsv`. Comments and blank lines are skipped."""
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        id_, pair, kind, expected_first, relevant, query = line.split("\t")
        questions.append(
            Question(
                id=id_,
                pair=pair,
                kind=Phrasing(kind),
                query=query,
                expected_first=int(expected_first),
                relevant=frozenset(int(n) for n in relevant.split(",")),
            )
        )
    return questions


_TRAILING_EXTENSION = re.compile(r"\.pdf$", re.IGNORECASE)


def lecture_ordinal(document_title: str) -> int | None:
    """Which lecture a document is, or `None` if it is not a lecture.

    Delegates to `read_ordinal` rather than matching `lec(\\d+)` here. That
    function is the one measured at 0 wrong over 70 real 6.006 filenames,
    including the `6`, `006` and `20` decoys that a naive digit grab reads as the
    lecture number -- `MIT6_006S20_lec7.pdf` has four numbers in it and only one
    of them is the answer. A second extractor written for the eval would be an
    unmeasured one, and it would be wrong in a way that looks like retrieval
    finding the wrong document.
    """
    ordinal = read_ordinal(_TRAILING_EXTENSION.sub("", document_title))
    if ordinal is None or ordinal[0] != "lecture":
        return None
    return ordinal[1]


def score_question(question: Question, hits: list[SearchHit]) -> QuestionResult:
    """Score one question against one ranker's hits.

    `hits` is expected to be `FETCH` deep; only the top `SCORE_AT` count towards
    the tally.
    """
    scored = hits[:SCORE_AT]
    outcome, badged, reason = _classify(question, scored)

    without_page1 = [hit for hit in hits if hit.page_number != 1][:SCORE_AT]
    outcome_without_page1, _, _ = _classify(question, without_page1)

    return QuestionResult(
        question=question,
        outcome=outcome,
        badged=badged,
        reason=reason,
        recall_at_10=_recall(question, hits[:10]),
        recall_at_20=_recall(question, hits[:SCORE_AT]),
        page1_in_top3=any(hit.page_number == 1 for hit in hits[:3]),
        outcome_without_page1=outcome_without_page1,
    )


def _classify(
    question: Question, hits: list[SearchHit]
) -> tuple[Outcome, tuple[int, ...], str | None]:
    """The four-way tally, through the same timeline the UI will render.

    Check order is deliberate and `not-found` deliberately outranks
    `first-wrong`. When the expected lecture is not in the results at all, the
    badged document is *necessarily* the wrong one, so both labels are true and
    only one of them is informative: "retrieval never found it" and "retrieval
    found it and mis-ordered it" call for completely different repairs.
    """
    if not hits:
        return Outcome.NOT_FOUND, (), None

    timeline = build_timeline(hits)

    if timeline.badge_suppressed:
        # Every document in this corpus is dated by hand, so this should be
        # unreachable here. If it fires, the finding is about the eval's corpus,
        # not about the ranker.
        return Outcome.UNRANKABLE, (), "an undated document matched"

    if timeline.earliest_count == 0:
        return Outcome.UNRANKABLE, (), "nothing dated matched"

    badged = tuple(
        sorted(
            ordinal
            for document in timeline.dated
            if document.is_earliest
            and (ordinal := lecture_ordinal(document.document_title)) is not None
        )
    )

    if len(badged) != timeline.earliest_count:
        return Outcome.UNRANKABLE, badged, "a badged document is not a lecture"

    retrieved = {
        ordinal
        for document in timeline.dated + timeline.undated
        if (ordinal := lecture_ordinal(document.document_title)) is not None
    }
    if question.expected_first not in retrieved:
        return Outcome.NOT_FOUND, badged, None

    if len(badged) > 1:
        return Outcome.UNRANKABLE, badged, "two documents tie for earliest"

    if badged[0] == question.expected_first:
        return Outcome.FIRST_CORRECT, badged, None

    return Outcome.FIRST_WRONG, badged, None


def _recall(question: Question, hits: list[SearchHit]) -> float:
    """Fraction of the topic's relevant lectures present in these hits.

    Against `relevant`, not `expected_first`: dynamic programming is four
    lectures, and a ranker that surfaces all four has done something a ranker
    that surfaces only the first has not. Document-level -- three chunks from one
    lecture are one lecture found.
    """
    found = {
        ordinal
        for hit in hits
        if (ordinal := lecture_ordinal(hit.document_title)) in question.relevant
    }
    return len(found) / len(question.relevant)


@dataclass(frozen=True)
class Tally:
    """Aggregated results for one ranker over one set of questions."""

    label: str
    total: int
    first_correct: int
    first_wrong: int
    not_found: int
    unrankable: int
    recall_at_10: float
    recall_at_20: float
    page1_in_top3: int

    # Of the `first_wrong` cases, how many become `first-correct` once page-1
    # chunks are removed. The number that decides whether boilerplate is a real
    # problem or a cosmetic one.
    first_wrong_caused_by_page1: int


def tally(label: str, results: list[QuestionResult]) -> Tally:
    total = len(results)
    return Tally(
        label=label,
        total=total,
        first_correct=_count(results, Outcome.FIRST_CORRECT),
        first_wrong=_count(results, Outcome.FIRST_WRONG),
        not_found=_count(results, Outcome.NOT_FOUND),
        unrankable=_count(results, Outcome.UNRANKABLE),
        recall_at_10=_mean(result.recall_at_10 for result in results),
        recall_at_20=_mean(result.recall_at_20 for result in results),
        page1_in_top3=sum(1 for result in results if result.page1_in_top3),
        first_wrong_caused_by_page1=sum(
            1
            for result in results
            if result.outcome == Outcome.FIRST_WRONG
            and result.outcome_without_page1 == Outcome.FIRST_CORRECT
        ),
    )


def _count(results: list[QuestionResult], outcome: Outcome) -> int:
    return sum(1 for result in results if result.outcome == outcome)


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0
