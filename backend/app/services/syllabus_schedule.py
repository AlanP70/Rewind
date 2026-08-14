"""Reading a schedule out of a syllabus, or saying honestly that we cannot.

A syllabus states its dates outright, which is what makes `parsed_syllabus` the
strongest source this phase has. Getting them out is the hard part: a schedule is
a table, `extract_text` returns lines, and whether the table survives depends
entirely on how it was laid out.

**One layout is recognised. Everything else is reported as unrecognised.**

    linear     one session per row, ordinal and date on the same line. The row
               *is* the line, so extraction cannot damage it.

    calendar   a week-by-week grid. Extraction flattens it into a run of dates
               and a run of labels, and the mapping between them lived in the
               column geometry, which is gone. `Lecture 13` and `Lecture 14` come
               out adjacent when one was Monday and the other Wednesday.

Both shapes are in `test-data/`, and the second is there on purpose. Nothing
short of re-reading the PDF's word coordinates can recover which lecture fell on
which date once the columns are gone, so this reports the format as unrecognised
and dates nothing. That refusal is the feature: a plausible-looking guess at a
calendar grid would be wrong by days, for every lecture, with nothing in the
output revealing it.

**Ordinals are read, never counted.** Waterloo's schedule has two unnumbered
`(-)` rows -- reading week, and a spare week at the end -- sitting between
numbered ones. Because the number comes from the row's own text, dropping those
rows cannot shift anything. Numbering by position instead would look identical on
a schedule with no gaps and silently move every week after reading week back by
one, which is this phase's failure mode in miniature: an answer that is confident,
plausible, uniformly wrong, and invisible in the result.

Pure -- text pages in, entries out, no session and no PDF -- for slice 2's
reason. A parser that needs a database to answer a question about a string cannot
be measured on a corpus.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.services import filename_dates

__all__ = ["ParsedSchedule", "ScheduleEntry", "parse_schedule"]

# Below this, whatever was found is not a schedule. Two lines that happen to
# begin with a number and a date occur in ordinary prose; twelve consecutive ones
# with rising dates do not.
MINIMUM_ROWS = 3

# How far above the first row to look for a line naming what is being numbered.
# Small on purpose: the further away the header, the more likely the word found
# belongs to something else on the page.
_HEADER_WINDOW = 6

# `(3)`, `(12)`, and the unnumbered `(-)`. The content is bounded so a topic in
# brackets cannot be mistaken for an ordinal.
_PAREN = re.compile(r"\s*\(\s*([^)\s]{1,4})\s*\)\s+")

# `3.` or `3)`.
_NUMBERED = re.compile(r"\s*(\d{1,2})[.)]\s+")

# `Week 3`, `Lecture 12`. The word has to be one `kind_for` knows, which is what
# stops `Room 3 Sep 20` reading as a session.
_WORD = re.compile(r"\s*([A-Za-z]{2,12})\s+(\d{1,2})\s+")


@dataclass(frozen=True)
class ScheduleEntry:
    """One dated session from a course's syllabus.

    `kind` uses the vocabulary `read_ordinal` returns -- `lecture`,
    `recitation`, `week` -- because that is what the two get joined on. It is
    whatever the syllabus says it numbers, never a translation of it: a schedule
    headed `Week of` produces `week` entries even when the course's files are
    named by lecture. See `parse_schedule`.
    """

    kind: str
    ordinal: int
    occurred_on: date


@dataclass(frozen=True)
class ParsedSchedule:
    """What a syllabus's schedule turned out to be.

    Two shapes, and `reason` is filled exactly when `entries` is empty -- the
    same convention `DatingOutcome` uses, for the same reason. A syllabus this
    cannot read is a result to report, not an error to swallow, and the caller
    has to be able to tell the user which of the two it was.

    `unit` is what the schedule numbers. `skipped` counts the rows that carried a
    date but no number -- reading week -- and it exists so a person reading `12
    weeks` off a fourteen-row table can tell that nothing went missing silently.

    It counts only the ones *between* the first and last numbered row. A dated,
    unnumbered line after the table has ended is indistinguishable from one that
    was never part of it, and Waterloo has both: `(-) Oct 11` for reading week,
    inside, and `(-) Dec 06` trailing after the last numbered week.
    """

    entries: tuple[ScheduleEntry, ...] = ()
    unit: str = ""
    skipped: int = 0
    reason: str = ""


@dataclass(frozen=True)
class _Row:
    """A line that begins with a session marker and a date."""

    index: int
    ordinal: int | None  # `None` for `(-)`: dated, deliberately unnumbered.
    unit: str | None  # Set only when the row names it, as in `Lecture 3`.
    occurred_on: date


def parse_schedule(
    pages: list[str], *, starts_on: date, ends_on: date
) -> ParsedSchedule:
    """Find the schedule in an extracted syllabus, or say why there isn't one.

    A schedule is **the longest run of consecutive rows whose ordinals and dates
    both strictly increase.** That single rule does three jobs, which is why it is
    worth stating as one: it finds the table among the rest of the document, it
    segments a syllabus that has more than one dated list in it, and it is the
    acceptance test. A calendar grid produces no such run because the lines
    carrying dates carry no ordinal and the lines carrying ordinals carry no date.

    Runs are of *rows*, not of lines, so the intervening text is irrelevant.
    Waterloo's longer topics wrap around their row -- the topic's first line sits
    above `(9) Nov 08` and the rest below it -- and none of that matters, because
    the join is on the ordinal and the topic text is never read. Layout that would
    defeat a topic-matching parser is invisible to this one.

    The term supplies the year, which most schedules omit; `Sep 06` in a Fall 2021
    course is unambiguous and `read_leading_date` refuses it when it is not.
    """
    lines = [line for page in pages for line in page.splitlines()]
    rows = [
        row
        for index, line in enumerate(lines)
        if (row := _read_row(index, line, starts_on=starts_on, ends_on=ends_on))
    ]

    run = _longest_run([row for row in rows if row.ordinal is not None])
    if len(run) < MINIMUM_ROWS:
        return ParsedSchedule(
            reason=(
                f"unrecognized schedule format: found {len(run)} row(s) with a "
                f"session number and a date on the same line, and at least "
                f"{MINIMUM_ROWS} are needed. A schedule laid out as a calendar "
                f"grid extracts as separate runs of dates and labels, and which "
                f"session fell on which date is not recoverable from the text"
            )
        )

    unit, reason = _unit(run, lines)
    if unit is None:
        return ParsedSchedule(reason=reason)

    return ParsedSchedule(
        entries=tuple(
            # `row.ordinal` is what the row says, not its position in the run.
            ScheduleEntry(kind=unit, ordinal=row.ordinal, occurred_on=row.occurred_on)
            for row in run
        ),
        unit=unit,
        skipped=sum(
            1
            for row in rows
            if row.ordinal is None and run[0].index < row.index < run[-1].index
        ),
    )


def _read_row(
    index: int, line: str, *, starts_on: date, ends_on: date
) -> _Row | None:
    """Read one line as `<session marker> <date> <anything>`, or `None`.

    The date must follow the marker immediately. `(3) Sep 20 Properties of
    algorithms` is a row; `(3) Properties of algorithms, moved from Sep 20` is
    not, and accepting it would date session 3 to a day the syllabus says it
    explicitly did *not* happen.
    """
    if match := _PAREN.match(line):
        token = match[1]
        # `(-)` and anything else non-numeric: a real row of the table, dated,
        # with no session number. Kept as a row so it can be counted, dropped
        # before numbering so it cannot be given one.
        ordinal = int(token) if token.isdigit() else None
        unit = None
    elif match := _NUMBERED.match(line):
        ordinal, unit = int(match[1]), None
    elif match := _WORD.match(line):
        unit = filename_dates.kind_for(match[1])
        if unit is None:
            return None
        ordinal = int(match[2])
    else:
        return None

    occurred_on = filename_dates.read_leading_date(
        line[match.end() :], starts_on=starts_on, ends_on=ends_on
    )
    if occurred_on is None:
        return None

    return _Row(index=index, ordinal=ordinal, unit=unit, occurred_on=occurred_on)


def _longest_run(rows: list[_Row]) -> list[_Row]:
    """The longest stretch of rows that both count up and move forward in time.

    Consecutive in document order rather than a longest increasing subsequence: a
    schedule is a contiguous block, and picking a rising subsequence out of
    scattered rows is exactly the kind of assembly this module refuses to do.
    """
    best: list[_Row] = []
    current: list[_Row] = []

    for row in rows:
        if current and not (
            row.ordinal > current[-1].ordinal
            and row.occurred_on > current[-1].occurred_on
        ):
            current = []
        current.append(row)
        if len(current) > len(best):
            best = list(current)

    return best


def _unit(run: list[_Row], lines: list[str]) -> tuple[str | None, str]:
    """What the schedule numbers, or `None` and the reason it is unclear.

    Rows first (`Lecture 3 Sep 20` says so outright), then the lines just above
    the table, which is where a schedule of bare `(3)` ordinals puts it --
    Waterloo's column header reads `Week of`.

    **An unnamed unit is a refusal, not a default to `lecture`.** The unit decides
    what a document has to be named to join, so guessing it wrong silently dates
    every document in the course from the wrong row, and `lecture` is the guess
    most likely to look right while being wrong -- weekly schedules are common and
    a week is not a lecture.
    """
    named = {row.unit for row in run if row.unit}
    if len(named) > 1:
        return None, (
            "unrecognized schedule format: its rows number more than one thing "
            f"({', '.join(sorted(named))}), so there is no single session series "
            "to join filenames against"
        )
    if named:
        return named.pop(), ""

    start = run[0].index
    for line in reversed(lines[max(0, start - _HEADER_WINDOW) : start]):
        for word in re.findall(r"[A-Za-z]+", line):
            if kind := filename_dates.kind_for(word):
                return kind, ""

    return None, (
        "unrecognized schedule format: the schedule is numbered but never says "
        "what it is numbering, and assuming lectures would date every document "
        "in the course from the wrong row if it is weekly"
    )
