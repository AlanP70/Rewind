"""Invariant 4, in a form that can fail.

`documents.occurred_at` is written by exactly one function. That rule protects a
write which does not exist yet -- Phase 5's cascade to `concept_mentions` -- so
for the whole of Phase 3 the funnel looks like a wrapper around one UPDATE and
reads as deletable. Prose in three documents says otherwise; prose does not fail
a test run.

Two assertions, because the rule has two halves:

  1. Nothing but `repositories/documents.py` writes the column.
  2. Nothing but `services/dating.py` calls the function that does.

**What this cannot do.** It reads source, not behaviour: a column name assembled
at runtime, or an UPDATE issued through raw text this does not recognise, walks
straight past it. It is a guard against the accident -- a later session adding
`occurred_at=` to a nearby query because that is where the data happened to be --
which is the failure that actually occurs. Determined circumvention needs the
constraint trigger Phase 5 brings.
"""

import ast

from app.core.paths import BACKEND_DIR

APP = BACKEND_DIR / "app"

COLUMN = "occurred_at"

# The one module allowed to write the column, and the one module allowed to ask
# it to. Both are paths relative to `app/`, so a file moving is a deliberate edit
# here rather than a silent pass.
WRITER = "repositories/documents.py"
WRITER_FUNCTION = "set_occurred_at"
CALLER = "services/dating.py"


def _sources() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(APP).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(APP.rglob("*.py"))
    ]


def _writes(tree: ast.Module) -> list[int]:
    """Line numbers of anything that could set the column in the database.

    Four shapes, which is all of them for this codebase: constructing a
    `Document(occurred_at=...)`, a SQLAlchemy `.values(occurred_at=...)`,
    assigning `something.occurred_at = ...`, and raw SQL naming the column in a
    statement that writes.

    Deliberately *not* every keyword argument called `occurred_at` -- schemas and
    dataclasses pass one around constantly, and a check that flags reading the
    value is a check that gets muted within a week.

    Docstrings are skipped, which is not a nicety. This whole rule is documented
    at length in the modules it governs, and the first run of this check failed on
    `redate_document`'s own docstring explaining that it updates the column: prose
    describing the invariant would otherwise be the thing that breaks it, and the
    obvious fix under time pressure is to delete the explanation. A bare string
    statement is a docstring; SQL is always an argument to something.
    """
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    lines: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name in {"Document", "values", "update", "insert"}:
                lines += [
                    node.lineno for kw in node.keywords if kw.arg == COLUMN
                ]

        elif isinstance(node, ast.Assign):
            lines += [
                node.lineno
                for target in node.targets
                if isinstance(target, ast.Attribute) and target.attr == COLUMN
            ]

        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
        ):
            text = node.value.upper()
            if COLUMN.upper() in text and ("UPDATE " in text or "INSERT " in text):
                lines.append(node.lineno)

    return sorted(set(lines))


def _calls(tree: ast.Module, function: str) -> list[int]:
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == function)
            or (isinstance(node.func, ast.Name) and node.func.id == function)
        )
    )


def test_only_the_documents_repository_writes_occurred_at() -> None:
    offenders = {
        module: lines
        for module, tree in _sources()
        if module != WRITER and (lines := _writes(tree))
    }
    assert not offenders, (
        f"{COLUMN} is written outside {WRITER}: {offenders}. "
        f"Route it through services.dating.redate_document -- Phase 5 attaches "
        f"the concept_mentions cascade there, and a second writer is a mention "
        f"table that silently disagrees with its documents."
    )


def test_only_dating_calls_the_writer() -> None:
    offenders = {
        module: lines
        for module, tree in _sources()
        if module not in {WRITER, CALLER} and (lines := _calls(tree, WRITER_FUNCTION))
    }
    assert not offenders, (
        f"{WRITER_FUNCTION} is called outside {CALLER}: {offenders}. "
        f"redate_document is the funnel; bypassing it skips the term-bounds check "
        f"now and the cascade later."
    )


def test_the_guard_can_actually_see_a_write() -> None:
    """The check above passes trivially if `_writes` never matches anything.

    A guard nobody has watched fail is a guard nobody knows is wired up -- this is
    the same reason Phase 1 proved `verify` could fail by fault injection rather
    than trusting a green run.
    """
    planted = ast.parse(
        "def f(session, when):\n"
        "    session.execute(update(Document).values(occurred_at=when))\n"
        "    document.occurred_at = when\n"
        '    session.execute("UPDATE documents SET occurred_at = now()")\n'
    )
    assert len(_writes(planted)) == 3


def test_the_guard_ignores_prose_about_the_column() -> None:
    """Describing the rule must not violate it.

    The first version of this check flagged `redate_document`'s docstring, which
    says in plain English that it updates `occurred_at`. A guard that punishes
    documentation gets satisfied by deleting the documentation.
    """
    documented = ast.parse(
        '"""This module will update occurred_at and insert occurred_at rows."""\n'
        "def f():\n"
        '    """Updates occurred_at. Does not INSERT occurred_at anywhere."""\n'
        "    return 1\n"
    )
    assert _writes(documented) == []
