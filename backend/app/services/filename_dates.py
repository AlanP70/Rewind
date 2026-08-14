"""Reading a date out of a filename, and the two different things that means.

A filename carries a date in one of two ways, and collapsing them would defeat
the point of `occurred_at_source`:

    `2020-02-11-dfs.pdf`   the date is *written down*. Read it, do not compute
                           it. It is wrong only if the person who named the file
                           was wrong.                        -> `filename_date`

    `lecture-07.pdf`       the filename carries an *ordinal*. A date exists only
                           after guessing where the 7th lecture fell, which
                           needs the course's term and the other filenames in
                           it.                          -> `inferred_filename`

These are different epistemic objects. The first is testimony; the second is
arithmetic performed on top of testimony about something else. That is why
migration 0005 split the enum rather than reusing one value for both.

Everything here is a pure function of a string plus the course's term -- no
session, no I/O. That is deliberate: a hit rate is only meaningful if it can be
measured by calling these directly on a list of filenames, without a database
standing in the way.

Two of the exports -- `kind_for` and `read_leading_date` -- are used by the
syllabus parser rather than by anything filename-shaped, and they live here
anyway. Both sides of the syllabus join need the same session vocabulary and the
same month table, and a second copy of either fails in the worst available way:
`week` against `weeks` joins nothing and looks like a syllabus that mentions no
sessions, and a divergent month table produces a wrong date rather than a crash.
One copy, under one set of tests.

**Anything not confidently recognised returns `None`.** Undated is an honest
outcome and the UI is built to show it; a wrong date is the outcome that
quietly corrupts a timeline. Every branch below that could go either way is
resolved toward `None`.
"""

import re
from datetime import date, timedelta

__all__ = [
    "interpolate",
    "kind_for",
    "read_explicit_date",
    "read_leading_date",
    "read_ordinal",
]

# Ordinal keywords, mapped to the *kind* of thing being numbered. The kind
# matters: a course holding `Lecture 1..20` and `Recitation 1..12` has two
# independent sequences, and interpolating them against one shared range would
# place recitation 12 at the end of term next to lecture 20.
_KINDS = {
    "lecture": "lecture",
    "lect": "lecture",
    "lec": "lecture",
    "recitation": "recitation",
    "rec": "recitation",
    "week": "week",
    "wk": "week",
    "problemset": "pset",
    "pset": "pset",
    "homework": "pset",
    "assignment": "pset",
    "hw": "pset",
    "ps": "pset",
    "session": "session",
    "class": "session",
    "day": "session",
}

# Longest first, so `lecture` wins before `lec` can match its prefix.
_KEYWORDS = "|".join(sorted(_KINDS, key=len, reverse=True))

# `lec18`, `Lecture 07`, `week-4`. The leading lookbehind stops a keyword being
# found inside a longer word, which is what keeps `mit6_006s20_lec18` reading as
# 18 rather than tripping over the `6`, `006` and `20` in front of it.
_ORDINAL = re.compile(
    rf"(?<![a-z0-9])({_KEYWORDS})[\s._-]*0*(\d{{1,2}})(?![0-9])", re.IGNORECASE
)

# `L3`, `l07`, `R12`. Restricted to a token that is *only* the letter and its
# digits, because a bare `l` allowed to match anywhere finds one in half the
# filenames in existence.
_LETTER_ORDINAL = re.compile(r"(?<![a-z0-9])([lr])0*(\d{1,2})(?![a-z0-9])", re.IGNORECASE)

_LETTER_KINDS = {"l": "lecture", "r": "recitation"}

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

# `2020-02-11`, `2020_02_11`, `20200211`. Digit lookarounds stop this biting a
# chunk out of a longer run of digits and calling it a date.
_ISO = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")

# `02-11-2020` -- two small numbers and a year, in an order the string does not
# record. See `read_explicit_date`.
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[-_./](\d{1,2})[-_./](\d{4})(?!\d)")

# `Feb 11`, `11-Feb`, `February 11 2020`. The year is optional; when it is
# missing the term supplies it, but only if the term makes it unambiguous.
_MONTH_FIRST = re.compile(
    rf"(?<![a-z])({_MONTH_NAMES})[\s._-]*(\d{{1,2}})(?![0-9])", re.IGNORECASE
)
_DAY_FIRST = re.compile(
    rf"(?<!\d)(\d{{1,2}})[\s._-]*({_MONTH_NAMES})(?![a-z])", re.IGNORECASE
)

# The same three shapes again, anchored to the start of the string and allowing a
# trailing year, for `read_leading_date`. Separate patterns rather than a shared
# one wrapped in `^`, because these are the ones written for prose: `Sept. 1,
# 2021` has a full stop and a comma that no filename ever contains.
_LEADING_ISO = re.compile(r"\s*(\d{4})-(\d{2})-(\d{2})(?!\d)")
_LEADING_MONTH_FIRST = re.compile(
    rf"\s*({_MONTH_NAMES})(?![a-z])\.?\s*(\d{{1,2}})(?!\d)(?:\s*,?\s*(\d{{4}})(?!\d))?",
    re.IGNORECASE,
)
_LEADING_DAY_FIRST = re.compile(
    rf"\s*(\d{{1,2}})(?!\d)\s*({_MONTH_NAMES})(?![a-z])\.?(?:\s*,?\s*(\d{{4}})(?!\d))?",
    re.IGNORECASE,
)


def kind_for(word: str) -> str | None:
    """The kind of session a single word names, or `None`.

    Public so the syllabus parser reads a schedule's unit from the same table
    `read_ordinal` reads a filename's. See the module docstring for why one copy
    matters more here than it usually does.
    """
    return _KINDS.get(word.lower())


def read_leading_date(text: str, *, starts_on: date, ends_on: date) -> date | None:
    """A date at the very start of `text`, or `None`.

    Anchored, where `read_explicit_date` searches, and that is the whole
    difference. A syllabus row is `(3) Sep 20 Properties of algorithms`: the date
    is the cell immediately after the ordinal, and a search would equally accept
    `(3) Properties of algorithms, revised Sep 20` -- where `Sep 20` is a
    parenthetical in the topic and dating the session by it would be wrong with
    no way to notice.

    A year written down wins; a missing one comes from the term on the same terms
    as everywhere else in this module, which is to say only when the term makes it
    unambiguous.
    """
    if match := _LEADING_ISO.match(text):
        return _date_or_none(int(match[1]), int(match[2]), int(match[3]))

    for pattern, month_group, day_group in (
        (_LEADING_MONTH_FIRST, 1, 2),
        (_LEADING_DAY_FIRST, 2, 1),
    ):
        if match := pattern.match(text):
            month = _MONTHS[match[month_group].lower()]
            day = int(match[day_group])
            if match[3]:
                return _date_or_none(int(match[3]), month, day)
            return _with_year_from_term(month, day, starts_on=starts_on, ends_on=ends_on)

    return None


def read_explicit_date(filename: str, *, starts_on: date, ends_on: date) -> date | None:
    """A date the filename actually states, or `None`.

    The term is passed in for one job only: supplying a missing year, and only
    when it can do so without choosing. `Feb-11.pdf` in a term running February
    to May is unambiguous; the same filename in a two-year archive is not, and
    then this returns `None` rather than picking the nearer one.

    `02-11-2020` is refused whenever both readings are real dates. February 11th
    and November 2nd are equally consistent with the string, the convention
    depends on which country the person naming the file grew up in, and a
    coin-flip between two valid dates is precisely the confident-and-wrong
    failure this phase exists to avoid. `13-02-2020` *is* read, because only one
    ordering survives -- there is no 13th month, so nothing is being guessed.
    """
    if match := _ISO.search(filename):
        return _date_or_none(int(match[1]), int(match[2]), int(match[3]))

    if match := _NUMERIC.search(filename):
        year = int(match[3])
        first, second = int(match[1]), int(match[2])
        day_first = _date_or_none(year, second, first)
        month_first = _date_or_none(year, first, second)
        if day_first and month_first:
            return None
        return day_first or month_first

    for pattern, month_group, day_group in (
        (_MONTH_FIRST, 1, 2),
        (_DAY_FIRST, 2, 1),
    ):
        if match := pattern.search(filename):
            month = _MONTHS[match[month_group].lower()]
            day = int(match[day_group])
            return _with_year_from_term(month, day, starts_on=starts_on, ends_on=ends_on)

    return None


def read_ordinal(filename: str) -> tuple[str, int] | None:
    """The `(kind, number)` a filename is numbered by, or `None`.

    No date is produced here. An ordinal on its own says nothing about when
    something happened -- turning it into a date is `interpolate`'s job, and
    needs every other filename in the course.
    """
    if match := _ORDINAL.search(filename):
        return _KINDS[match[1].lower()], int(match[2])

    if match := _LETTER_ORDINAL.search(filename):
        return _LETTER_KINDS[match[1].lower()], int(match[2])

    return None


def interpolate(
    ordinal: int, *, lowest: int, highest: int, starts_on: date, ends_on: date
) -> date | None:
    """Place ordinal `n` in the term, given the range of ordinals observed.

    Spread across the *observed* range rather than the term as a whole. If a
    course's numbering runs 1..20, lecture 1 lands on the first day of term and
    lecture 20 on the last, which is roughly true of a real course and exactly
    true of its endpoints.

    **A single ordinal returns `None`**, because `lowest == highest` describes no
    range to interpolate across. That is the useful property, not an edge case
    handled grudgingly: one file called `lecture-07.pdf` gives no evidence about
    where lecture 7 fell, and the alternative -- assuming a 7th of something is
    a third of the way in, or dropping it at the start of term -- invents a fact.
    Undated is the correct answer and it falls out of the arithmetic.

    What this cannot do is know about the shape of a real timetable. Lectures
    are not evenly spaced: a Tuesday/Thursday course alternates 2- and 5-day
    gaps, and a spring break puts a week-long hole in the middle that this
    spreads evenly over the whole term instead. So the *ordering* it produces is
    always right -- interpolation is monotonic in the ordinal -- while the dates
    drift. That asymmetry is why this is `inferred_filename` and not
    `filename_date`.
    """
    if highest <= lowest:
        return None

    span = (ends_on - starts_on).days
    offset = round(span * (ordinal - lowest) / (highest - lowest))
    return starts_on + timedelta(days=offset)


def _date_or_none(year: int, month: int, day: int) -> date | None:
    """`date()` without the exception. Feb 30th is not a date, it is a typo."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _with_year_from_term(
    month: int, day: int, *, starts_on: date, ends_on: date
) -> date | None:
    """Attach the only year that puts this month and day inside the term.

    Two candidates at most, since no course term spans three January-to-Januarys
    in any calendar this product cares about. Exactly one hit is an answer; zero
    means the date is outside the term and `redate_document` would refuse it
    anyway; two means a term long enough to contain the same date twice, where
    choosing would be guessing.
    """
    hits = [
        candidate
        for year in range(starts_on.year, ends_on.year + 1)
        if (candidate := _date_or_none(year, month, day))
        and starts_on <= candidate <= ends_on
    ]
    return hits[0] if len(hits) == 1 else None
