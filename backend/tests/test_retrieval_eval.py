"""The eval's scoring, plus a structural check on the pre-registered questions.

Pure. Hits are hand-built so the correct outcome is known by construction, which
is the only way to tell a scoring bug from a retrieval result -- if the scorer
could only be exercised by running the real thing, its bugs would arrive looking
like conclusions about retrieval.

The four-way tally is the thing this project reports, so each of the four
outcomes has a test that the other three would fail.
"""

from datetime import UTC, datetime

import pytest

from app.core.paths import BACKEND_DIR
from app.services.retrieval_eval import (
    Outcome,
    Phrasing,
    Question,
    lecture_ordinal,
    load_questions,
    score_question,
    tally,
)
from tests.test_timeline import hit, on

QUESTIONS = BACKEND_DIR / "evals" / "questions.tsv"


def question(expected_first: int = 3, relevant: set[int] | None = None) -> Question:
    return Question(
        id="q00",
        pair="sorting",
        kind=Phrasing.LITERAL,
        query="Where did I first learn about sorting?",
        expected_first=expected_first,
        relevant=frozenset(relevant or {expected_first}),
    )


def lecture(number: int, *, distance: float, day: int, page_number: int = 2):
    """A hit in `MIT6_006S20_lecN.pdf`, dated so that lecture order is date order."""
    return hit(
        f"MIT6_006S20_lec{number}",
        distance=distance,
        occurred_at=datetime(2020, 3, 1, tzinfo=UTC).replace(day=day),
        page_number=page_number,
    )


# --------------------------------------------------------------------------
# Mapping a document back to a lecture number
# --------------------------------------------------------------------------


def test_lecture_ordinal_ignores_the_decoy_numbers_in_the_filename() -> None:
    """`MIT6_006S20_lec7` has four numbers in it and only one is the answer.

    A naive digit grab returns 6, which would map every lecture in the corpus to
    lecture 6 and report it as retrieval finding the wrong document. This
    delegates to `read_ordinal`, which is the extractor measured at 0 wrong over
    70 real filenames.
    """
    assert lecture_ordinal("MIT6_006S20_lec7") == 7
    assert lecture_ordinal("MIT6_006S20_lec7.pdf") == 7
    # Lecture 18 is served under a lowercase name by OCW. One document out of
    # twenty, which is exactly the size of bug that survives a spot check.
    assert lecture_ordinal("mit6_006s20_lec18.pdf") == 18


def test_lecture_ordinal_declines_things_that_are_not_lectures() -> None:
    assert lecture_ordinal("MIT6_006S20_quiz1") is None
    assert lecture_ordinal("syllabus") is None


# --------------------------------------------------------------------------
# The four outcomes
# --------------------------------------------------------------------------


def test_first_correct_when_the_oldest_matched_lecture_is_the_expected_one() -> None:
    result = score_question(
        question(expected_first=3),
        [
            lecture(15, distance=0.10, day=15),
            lecture(3, distance=0.40, day=3),
        ],
    )

    # The expected lecture is the *worst* match here. Scoring "first occurrence"
    # correctly means the ranking decides what matched and the dates decide what
    # is first, and mixing the two would flip this to first-wrong.
    assert result.outcome is Outcome.FIRST_CORRECT
    assert result.badged == (3,)


def test_first_wrong_when_an_earlier_lecture_matched_instead() -> None:
    result = score_question(
        question(expected_first=3),
        [
            lecture(3, distance=0.10, day=3),
            lecture(2, distance=0.40, day=2),
        ],
    )

    assert result.outcome is Outcome.FIRST_WRONG
    assert result.badged == (2,)


def test_not_found_outranks_first_wrong_when_the_answer_is_absent() -> None:
    """Both labels are true when the expected lecture is missing; only one is
    useful. "Never found it" and "found it and mis-ordered it" have completely
    different repairs, so the more specific one wins."""
    result = score_question(
        question(expected_first=3),
        [
            lecture(9, distance=0.10, day=9),
            lecture(15, distance=0.20, day=15),
        ],
    )

    assert result.outcome is Outcome.NOT_FOUND


def test_unrankable_when_an_undated_document_matched() -> None:
    """Should be unreachable on this corpus -- every document is dated by hand.
    If it fires in a real run the finding is about the corpus, not the ranker,
    which is why the reason travels with the outcome."""
    result = score_question(
        question(expected_first=3),
        [lecture(3, distance=0.10, day=3), hit("mystery", distance=0.5, occurred_at=None)],
    )

    assert result.outcome is Outcome.UNRANKABLE
    assert result.reason == "an undated document matched"


def test_unrankable_when_two_lectures_tie_for_earliest() -> None:
    """The timeline badges both, so the eval cannot claim the ranker named a
    first. Counting a tie as correct because the right answer is in it would
    make a ranker that badged everything score 100%."""
    result = score_question(
        question(expected_first=3),
        [lecture(3, distance=0.10, day=3), lecture(4, distance=0.20, day=3)],
    )

    assert result.outcome is Outcome.UNRANKABLE
    assert result.reason == "two documents tie for earliest"
    assert result.badged == (3, 4)


def test_no_hits_at_all_is_not_found() -> None:
    assert score_question(question(), []).outcome is Outcome.NOT_FOUND


# --------------------------------------------------------------------------
# Recall
# --------------------------------------------------------------------------


def test_recall_is_over_every_relevant_lecture_not_just_the_first() -> None:
    """Dynamic programming is four lectures. A ranker that surfaces two of them
    has done something a ranker that surfaces one has not, and scoring only
    `expected_first` would call those identical."""
    result = score_question(
        question(expected_first=15, relevant={15, 16, 17, 18}),
        [
            lecture(15, distance=0.10, day=15),
            lecture(17, distance=0.20, day=17),
            lecture(9, distance=0.30, day=9),
        ],
    )

    assert result.recall_at_20 == pytest.approx(0.5)


def test_recall_counts_documents_not_chunks() -> None:
    """Five passages from one lecture are one lecture found. Counting chunks
    would let a single well-matched deck score above 1.0."""
    result = score_question(
        question(expected_first=15, relevant={15, 16}),
        [lecture(15, distance=0.1 * n, day=15) for n in range(1, 6)],
    )

    assert result.recall_at_20 == pytest.approx(0.5)


def test_recall_at_10_uses_only_the_first_ten_hits() -> None:
    """A cutoff that ignores k reports recall@20 twice and the two columns agree
    for reasons that look like a finding."""
    hits = [lecture(9, distance=0.01 * n, day=9) for n in range(10)]
    hits.append(lecture(3, distance=0.9, day=3))

    result = score_question(question(expected_first=3, relevant={3}), hits)

    assert result.recall_at_10 == pytest.approx(0.0)
    assert result.recall_at_20 == pytest.approx(1.0)


# --------------------------------------------------------------------------
# The page-1 boilerplate counterfactual
# --------------------------------------------------------------------------


def test_page1_counterfactual_detects_a_header_that_cost_the_answer() -> None:
    """The number the phase actually asked for: not "is boilerplate present" but
    "did boilerplate cost an answer". Lecture 2's page-1 header is the only
    reason lecture 2 is in these results at all."""
    result = score_question(
        question(expected_first=3),
        [
            lecture(2, distance=0.10, day=2, page_number=1),
            lecture(3, distance=0.20, day=3),
        ],
    )

    assert result.page1_in_top3 is True
    assert result.outcome is Outcome.FIRST_WRONG
    assert result.outcome_without_page1 is Outcome.FIRST_CORRECT
    assert tally("vector", [result]).first_wrong_caused_by_page1 == 1


def test_a_page1_hit_that_costs_nothing_is_counted_separately() -> None:
    """Present but harmless. If every `first-wrong` survives the counterfactual,
    boilerplate is cosmetic and a header filter should not be argued for on this
    evidence."""
    result = score_question(
        question(expected_first=3),
        [
            lecture(3, distance=0.10, day=3, page_number=1),
            lecture(9, distance=0.20, day=9),
        ],
    )

    assert result.page1_in_top3 is True
    assert result.outcome is Outcome.FIRST_CORRECT
    assert tally("vector", [result]).first_wrong_caused_by_page1 == 0


# --------------------------------------------------------------------------
# The pre-registered question file
# --------------------------------------------------------------------------


def test_the_committed_questions_are_sixteen_matched_pairs() -> None:
    """The design, asserted rather than described.

    `questions.tsv` was committed before this module existed. What makes it a
    controlled comparison rather than two unrelated question sets is that every
    topic appears exactly twice with the same expected answer -- so a gap between
    the literal and paraphrase rows is the phrasing and not the topic.
    """
    questions = load_questions(QUESTIONS)

    assert len(questions) == 16

    by_pair: dict[str, list[Question]] = {}
    for item in questions:
        by_pair.setdefault(item.pair, []).append(item)

    assert len(by_pair) == 8
    for pair, both in by_pair.items():
        assert {item.kind for item in both} == {Phrasing.LITERAL, Phrasing.PARAPHRASE}, pair
        assert both[0].expected_first == both[1].expected_first, pair
        assert both[0].relevant == both[1].relevant, pair


def test_every_question_expects_the_lowest_relevant_lecture() -> None:
    """`expected_first` is derived from `relevant`, not typed independently. A
    hand-typed pair can disagree, and the eval would then mark a correct answer
    wrong with no error anywhere."""
    for item in load_questions(QUESTIONS):
        assert item.expected_first == min(item.relevant), item.id


def test_no_paraphrase_reuses_its_own_topic_wording() -> None:
    """The paraphrase half only measures anything if the title vocabulary is
    actually gone. `hashing` inside the hashing paraphrase would silently turn
    this into a second literal question and inflate the half of the eval that
    exists to be harder.
    """
    forbidden = {
        "sorting": ("sort",),
        "hashing": ("hash",),
        "binary-trees": ("binary", "tree"),
        "binary-heaps": ("binary", "heap"),
        "bfs": ("breadth",),
        "dfs": ("depth",),
        "dijkstra": ("dijkstra",),
        "dynamic-programming": ("dynamic", "programming"),
    }
    for item in load_questions(QUESTIONS):
        if item.kind is Phrasing.PARAPHRASE:
            lowered = item.query.lower()
            for word in forbidden[item.pair]:
                assert word not in lowered, f"{item.id} still says {word!r}"
