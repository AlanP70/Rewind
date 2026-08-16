"""Filename parsing, against a corpus nobody wrote for it.

Pure functions, so no database. The last test is the one that matters: it runs
the parser over 70 real MIT 6.006 filenames and pins the outcome, broken down
into correct / undated / wrong rather than a single percentage. Those three have
different costs -- undated is honest and the UI is built to show it, wrong is the
one that puts a lecture in the wrong week and quietly discredits the timeline --
so an aggregate that lets one move while another compensates is the wrong number
to watch.

It now reads 70/0/0, and that is a weaker claim than 70/70 sounds. Twelve of the
rows were fitted after the fact, and the reason to keep the pin anyway is 0
wrong. See that test's docstring.
"""

from datetime import date
from pathlib import Path

import pytest

from app.services.filename_dates import interpolate, read_explicit_date, read_ordinal

STARTS_ON = date(2020, 2, 3)
ENDS_ON = date(2020, 5, 12)

CORPUS = Path(__file__).parent / "data" / "ocw_6006_s20_filenames.tsv"


def explicit(filename: str) -> date | None:
    return read_explicit_date(filename, starts_on=STARTS_ON, ends_on=ENDS_ON)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2020-02-11-dfs.pdf", date(2020, 2, 11)),
        ("2020_02_11 lecture.pdf", date(2020, 2, 11)),
        ("20200211.pdf", date(2020, 2, 11)),
        ("scan 11 Feb 2020.pdf", date(2020, 2, 11)),
        ("February-11-2020.pdf", date(2020, 2, 11)),
        # No year in the filename; the term contains exactly one 11th of February.
        ("Feb 11 notes.pdf", date(2020, 2, 11)),
        ("11-feb-recursion.pdf", date(2020, 2, 11)),
    ],
)
def test_a_stated_date_is_read(filename: str, expected: date) -> None:
    assert explicit(filename) == expected


def test_an_unambiguous_day_first_date_is_read() -> None:
    """`13-02-2020` has only one reading -- there is no thirteenth month."""
    assert explicit("13-02-2020.pdf") == date(2020, 2, 13)


def test_an_ambiguous_numeric_date_is_refused() -> None:
    """February 11th and November 2nd are equally consistent with `02-11-2020`.

    Which one a filename means depends on where the person naming it grew up.
    A coin-flip between two real dates is the confident-and-wrong failure this
    phase exists to avoid, so this is `None` -- undated, and visible as such.
    """
    assert explicit("02-11-2020.pdf") is None


def test_an_impossible_date_is_not_a_date() -> None:
    assert explicit("2020-02-30.pdf") is None


def test_a_missing_year_the_term_cannot_settle_is_refused() -> None:
    """A term containing two 11ths of February cannot say which one is meant.

    Needs a term spanning more than a year: an autumn-to-autumn range still holds
    only one February, and the year is then not a guess at all.
    """
    assert (
        read_explicit_date(
            "Feb 11.pdf", starts_on=date(2019, 1, 1), ends_on=date(2020, 12, 15)
        )
        is None
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Lecture 07.pdf", ("lecture", 7)),
        ("lecture-7.pdf", ("lecture", 7)),
        ("lec03-dfs.pdf", ("lecture", 3)),
        ("L12.pdf", ("lecture", 12)),
        ("recitation_5.pdf", ("recitation", 5)),
        ("Week 4 notes.pdf", ("week", 4)),
        ("pset3.pdf", ("pset", 3)),
        ("hw2 solutions.pdf", ("pset", 2)),
        ("review2_sol.pdf", ("review", 2)),
        # `quiz` is a keyword; `q` is a letter ordinal, like `l` and `r`.
        ("quiz1.pdf", ("quiz", 1)),
        ("mit6_006s20_q1.pdf", ("quiz", 1)),
        # The `q`-means-question collision, and the only fixture here that can
        # catch it: a letter-ordinal-first parser reads this as quiz 3, and
        # `ps5_questions.pdf` would not tell the two apart because `q` needs
        # digits behind it. Keywords are tried before single letters.
        ("ps5_q3.pdf", ("pset", 5)),
        # Three decoy numbers before the real one.
        ("MIT6_006S20_lec18.pdf", ("lecture", 18)),
    ],
)
def test_an_ordinal_is_read(filename: str, expected: tuple[str, int]) -> None:
    assert read_ordinal(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "notes.pdf",
        "6.006 syllabus.pdf",
        "final.pdf",
        # A bare letter allowed to match anywhere finds an `l` in half the
        # filenames in existence, so `L\d` only counts as a whole token.
        "algorithms.pdf",
    ],
)
def test_a_filename_with_no_ordinal_returns_nothing(filename: str) -> None:
    assert read_ordinal(filename) is None


def test_an_ordinal_is_placed_across_the_observed_range() -> None:
    """Ordinal 1 lands on the first day of term, the highest on the last."""
    common = {"lowest": 1, "highest": 21, "starts_on": STARTS_ON, "ends_on": ENDS_ON}

    assert interpolate(1, **common) == STARTS_ON
    assert interpolate(21, **common) == ENDS_ON
    # Exactly halfway through a 99-day term: 49.5 days, rounded to 50.
    assert interpolate(11, **common) == date(2020, 3, 24)


def test_a_lone_ordinal_cannot_be_placed() -> None:
    """The property the whole design leans on.

    One file called `lecture-07.pdf` is no evidence about where lecture 7 fell.
    Undated is the correct answer and it falls out of the arithmetic rather than
    being a special case someone has to remember to write.
    """
    assert (
        interpolate(7, lowest=7, highest=7, starts_on=STARTS_ON, ends_on=ENDS_ON) is None
    )


def test_interpolation_never_reorders_lectures() -> None:
    """The dates drift; the ordering does not.

    Interpolation is monotonic in the ordinal, so even when a real timetable's
    holidays make the dates wrong by days, lecture 4 still sorts before lecture 5.
    That is what makes `inferred_filename` usable for a timeline at all, and the
    reason it is a weaker source than `filename_date` rather than a useless one.
    """
    placed = [
        interpolate(n, lowest=1, highest=20, starts_on=STARTS_ON, ends_on=ENDS_ON)
        for n in range(1, 21)
    ]

    assert placed == sorted(placed)


def _corpus() -> list[tuple[str, str]]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [line.split("\t") for line in lines if line and not line.startswith("#")]
    return [(filename, label) for filename, label in rows]


def test_the_corpus_is_the_size_it_claims_to_be() -> None:
    """Guards the number below against a truncated data file passing quietly."""
    assert len(_corpus()) == 70


def test_measured_extraction_on_real_filenames() -> None:
    """70 correct, 0 undated, 0 wrong, on 70 filenames written by MIT in 2020.

    **12 of those 70 are fitted, not tested.** The earlier run scored 58/12/0,
    and the 12 undated were the three quizzes and three review sessions with
    their solution files -- `_q1`, `_review2_sol`. `quiz`, `review` and the `q`
    letter ordinal were then added *because of* these rows. A rule read off this
    corpus and scored on this corpus reaches 70 by construction; those twelve
    say nothing about whether the same shapes are recognised elsewhere, and only
    a corpus from another course could.

    **0 wrong is what survives that**, and it is the load-bearing figure. Every
    one of these names carries `6`, `006` and `20` in front of the ordinal, and
    clearing those decoys is a property of the parser rather than of its
    vocabulary -- the twelve new hits had to clear them too. A parser that
    grabbed the wrong number would produce a confident date in the wrong month.

    What this does not measure: whether an interpolated date is *right*. These
    filenames carry no dates, so nothing here compares an inferred date to a real
    lecture date. See ROADMAP, Phase 3.
    """
    outcomes = {"correct": 0, "undated": 0, "wrong": 0}
    for filename, label in _corpus():
        expected = (
            (label.split(":")[0], int(label.split(":")[1])) if label != "-" else None
        )
        got = read_ordinal(filename)
        if got == expected:
            outcomes["correct"] += 1
        elif got is None:
            outcomes["undated"] += 1
        else:
            outcomes["wrong"] += 1

    assert outcomes == {"correct": 70, "undated": 0, "wrong": 0}
