"""`parse_schedule` — one real syllabus it reads, one it refuses.

**Two worked examples, one positive and one negative. Not a hit rate.** Slice 2's
filename figure was a measurement because 70 labelled filenames is a sample; two
syllabi are not, and no number derived from them should be written up as though
they were. What these two do establish is that the recognised layout exists in the
wild and that the refused one does too — which is the claim the parser is built
on, and the reason it was not built earlier against an invented format.

Both fixtures are the real PDFs' extracted text, committed whole rather than
trimmed to the schedule page, so the parser has to find the table among the rest
of the document the way it will in production. `test_fixtures_still_match_the_pdfs`
re-extracts and compares, because a fixture nobody re-derives from its source
drifts into being an invention — the exact failure this slice waited to avoid.
"""

from datetime import date
from pathlib import Path

import pytest

from app.core.paths import BACKEND_DIR
from app.services.extraction import extract_pages
from app.services.syllabus_schedule import MINIMUM_ROWS, ScheduleEntry, parse_schedule

DATA = BACKEND_DIR / "tests" / "data"
SYLLABI = BACKEND_DIR.parent / "test-data"

# Fall 2021 at Waterloo. The schedule writes `Sep 06` with no year anywhere on
# the row, so these bounds are what supplies it.
WATERLOO = dict(starts_on=date(2021, 9, 1), ends_on=date(2021, 12, 23))

# Winter 2026 at York.
YORK = dict(starts_on=date(2026, 1, 5), ends_on=date(2026, 4, 24))


def pages(name: str) -> list[str]:
    return DATA.joinpath(name).read_text(encoding="utf-8").split("\f")


@pytest.fixture
def waterloo() -> list[str]:
    return pages("ece606_f2021_syllabus.txt")


@pytest.fixture
def york() -> list[str]:
    return pages("eecs3101_w26_syllabus.txt")


def test_reads_a_linear_schedule(waterloo):
    """ECE 606's Content Schedule: one session per row, ordinal and date on it."""
    parsed = parse_schedule(waterloo, **WATERLOO)

    assert parsed.reason == ""
    assert [entry.ordinal for entry in parsed.entries] == list(range(1, 13))
    assert parsed.entries[0].occurred_on == date(2021, 9, 6)
    assert parsed.entries[-1].occurred_on == date(2021, 11, 29)


def test_the_unit_comes_from_the_column_header(waterloo):
    """The rows are bare `(1)`, `(2)`; `Week of` above them is what names them.

    Load-bearing rather than cosmetic. `week` is what a filename has to be
    numbered by to join, so reading this wrong — or defaulting it to `lecture` —
    would date every document in the course from the wrong row.
    """
    parsed = parse_schedule(waterloo, **WATERLOO)

    assert parsed.unit == "week"
    assert all(entry.kind == "week" for entry in parsed.entries)


def test_reading_week_is_dropped_and_counted(waterloo):
    """`(-) Oct 11` dates nothing, and says so rather than vanishing.

    The count is what lets someone reading `12 weeks` off a fourteen-row table
    tell that two rows were deliberately dropped rather than quietly lost. Only
    the interior one is counted here — `(-) Dec 06` trails the last numbered week
    and cannot be distinguished from a line that was never in the table.
    """
    parsed = parse_schedule(waterloo, **WATERLOO)

    assert parsed.skipped == 1
    assert date(2021, 10, 11) not in [entry.occurred_on for entry in parsed.entries]


def test_a_gap_in_the_numbering_is_preserved():
    """Weeks 1, 2, 4, 5 stay 1, 2, 4, 5. This is where reading rather than
    counting can actually be observed.

    ECE 606's own numbering runs 1..12 with no gaps, so numbering its rows by
    position would produce an identical result and the fixture above cannot tell
    the two apart — which is exactly how the mistake survives review. A syllabus
    that omits a cancelled session can, and `week 4` has to keep meaning what the
    syllabus said, because that is the number a filename will carry.
    """
    parsed = parse_schedule(
        [
            "Week of Topics",
            "(1) Sep 06 Intro",
            "(2) Sep 13 Data structures",
            "(4) Sep 27 Incremental design",
            "(5) Oct 04 Divide and conquer",
        ],
        **WATERLOO,
    )

    assert [entry.ordinal for entry in parsed.entries] == [1, 2, 4, 5]
    assert parsed.entries[2].occurred_on == date(2021, 9, 27)


def test_an_unnumbered_row_consumes_no_number(waterloo):
    """Reading week is dropped, and drops no number with it.

    The mistake this rules out is treating `(-) Oct 11` as the sixth row of the
    table. Week 6 would then take Oct 11 and every week after it would slide back
    seven days, all the way to the end of term — a result that looks exactly as
    orderly as the correct one and is wrong in the second half of every course
    with a reading week.
    """
    parsed = parse_schedule(waterloo, **WATERLOO)

    assert len(parsed.entries) == 12
    assert parsed.entries[5] == ScheduleEntry(
        kind="week", ordinal=6, occurred_on=date(2021, 10, 18)
    )


def test_the_exam_period_line_is_not_a_session(waterloo):
    """`Dec 9 – 23, final exam` carries a date and no session number.

    It is the line most likely to be swept in by a looser rule, and it would add a
    thirteenth week dated three weeks past the end of teaching.
    """
    parsed = parse_schedule(waterloo, **WATERLOO)

    assert max(entry.occurred_on for entry in parsed.entries) == date(2021, 11, 29)


def test_refuses_a_calendar_grid(york):
    """EECS 3101's schedule is a grid, and the grid does not survive extraction.

    The dates come out as one run (`12 13 14 15 16`) and the labels as another
    (`Lecture 3 Lecture 4 Tutorial 2`), with three labels against five columns.
    Which lecture fell on which date lived in the cell geometry. There is no
    honest way to reconstruct it from the text, so nothing is dated.
    """
    parsed = parse_schedule(york, **YORK)

    assert parsed.entries == ()
    assert parsed.unit == ""
    assert "unrecognized schedule format" in parsed.reason


def test_the_refusal_says_what_was_missing(york):
    """A refusal a person cannot act on is only half an answer.

    The reason names the shape the parser needs, so the next step — transcribing
    a dozen rows into `--schedule` — is obvious rather than a support question.
    """
    parsed = parse_schedule(york, **YORK)

    assert "session number and a date on the same line" in parsed.reason
    assert "calendar grid" in parsed.reason


def test_fixtures_still_match_the_pdfs(waterloo, york):
    """The committed text is what `extract_pages` produces today, not a memory.

    Skipped rather than failed when the PDFs are absent, so a clone without
    `test-data/` still runs the suite — but never skipped silently in a checkout
    that has them.
    """
    for name, expected in (
        ("ece_606_syllabus-logistics-schedule_3.pdf", waterloo),
        ("EECS3101-W26-Syllabus-York.pdf", york),
    ):
        source = SYLLABI / name
        if not source.is_file():
            pytest.skip(f"{source} not present")
        assert extract_pages(source.read_bytes(), name=name) == expected


def test_a_date_must_follow_the_ordinal_immediately():
    """`(3) ... Sep 20` in prose is not row 3 happening on the 20th.

    The date has to be the cell after the number. A syllabus saying a session was
    *moved from* a date would otherwise be read as saying it happened then, which
    is the one reading the sentence rules out.
    """
    parsed = parse_schedule(
        [
            "Lecture Schedule",
            "(1) Sep 06 Intro",
            "(2) Sep 13 Data structures",
            "(3) Properties of algorithms, moved from Sep 20",
        ],
        **WATERLOO,
    )

    assert parsed.entries == ()


def test_refuses_a_schedule_that_never_says_what_it_numbers():
    """Bare ordinals, no `Week of`, no `Lecture` anywhere above them.

    Refused rather than assumed to be lectures. The unit decides which documents
    can join at all, and `lecture` is the assumption most likely to look right
    while being wrong, since weekly schedules are common and a week is not a
    lecture.
    """
    parsed = parse_schedule(
        [
            "Topics covered",
            "(1) Sep 06 Intro",
            "(2) Sep 13 Data structures",
            "(3) Sep 20 Properties of algorithms",
        ],
        **WATERLOO,
    )

    assert parsed.entries == ()
    assert "never says what it is numbering" in parsed.reason


def test_interleaved_series_are_refused_by_the_run_rule():
    """A table alternating lectures and recitations counts 1, 1, 2, 2.

    Two series sharing a table do not form one rising run, so this is refused
    before the unit is ever considered. Worth pinning as the realistic shape of
    the mixed-table problem — the guard below catches a rarer one.
    """
    parsed = parse_schedule(
        [
            "Lecture 1 Sep 06 Intro",
            "Recitation 1 Sep 08 Setup",
            "Lecture 2 Sep 13 Data structures",
            "Recitation 2 Sep 15 Practice",
        ],
        **WATERLOO,
    )

    assert parsed.entries == ()


def test_refuses_a_rising_run_that_numbers_two_different_things():
    """Rows that do rise, but do not agree on what they are counting.

    Rarer than the interleaved case above and kept separate from it, because the
    failure without this branch is not a refusal but an arbitrary answer: the unit
    would be whichever of the two a set happened to yield first, and the whole
    course would then join — or not — on a coin flip nothing recorded.
    """
    parsed = parse_schedule(
        [
            "Lecture 1 Sep 06 Intro",
            "Recitation 2 Sep 08 Setup",
            "Lecture 3 Sep 13 Data structures",
        ],
        **WATERLOO,
    )

    assert parsed.entries == ()
    assert "more than one thing" in parsed.reason


def test_two_rows_are_not_a_schedule():
    """Below `MINIMUM_ROWS`, because two such lines occur in ordinary prose."""
    parsed = parse_schedule(
        ["Lecture Schedule", "(1) Sep 06 Intro", "(2) Sep 13 Data structures"],
        **WATERLOO,
    )

    assert parsed.entries == ()
    assert str(MINIMUM_ROWS) in parsed.reason


def test_takes_the_longest_run_when_a_document_holds_two_dated_lists():
    """A syllabus with an assignment list as well as a schedule.

    The rule that accepts a schedule is also the rule that separates it from
    anything else: each list is its own run of rising ordinals and dates, and the
    longer one wins. The shorter list is not merged in, which would produce a
    series that counts 1, 2, 1, 2.
    """
    parsed = parse_schedule(
        [
            "Assignments",
            "(1) Sep 10 Assignment 1 due",
            "(2) Sep 24 Assignment 2 due",
            "Lecture Schedule",
            "(1) Sep 06 Intro",
            "(2) Sep 13 Data structures",
            "(3) Sep 20 Properties of algorithms",
            "(4) Sep 27 Incremental design",
        ],
        **WATERLOO,
    )

    assert parsed.unit == "lecture"
    assert [entry.ordinal for entry in parsed.entries] == [1, 2, 3, 4]
    assert parsed.entries[0].occurred_on == date(2021, 9, 6)


def test_a_year_on_the_row_beats_the_term():
    """A schedule spanning a New Year states its years, and they win."""
    parsed = parse_schedule(
        [
            "Lecture Schedule",
            "Lecture 1 Dec 6, 2021 Intro",
            "Lecture 2 Dec 13, 2021 Data structures",
            "Lecture 3 Dec 20, 2021 Properties",
        ],
        starts_on=date(2021, 9, 1),
        ends_on=date(2022, 4, 30),
    )

    assert [entry.occurred_on for entry in parsed.entries] == [
        date(2021, 12, 6),
        date(2021, 12, 13),
        date(2021, 12, 20),
    ]
