"""Where things are on disk, and how `documents.storage_path` is written.

Separate from `config.py` because `config.py` instantiates `Settings` at import
and therefore needs a `.env`. The `verify` subprocess re-extracts a PDF and needs
the repo root but no configuration at all.
"""

from pathlib import Path

# app/core/paths.py -> app/core -> app -> backend.
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


def to_storage_path(path: Path) -> str:
    """Absolute path in, the value `documents.storage_path` stores out.

    Relative to the repo root and POSIX-separated, so the same row means the same
    file on another machine. An absolute path here would pin every document -- and
    so every `verify` run -- to one laptop.
    """
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        raise ValueError(
            f"{path} is outside the repo root {REPO_ROOT}; "
            "Phase 1 ingests files from within the repo"
        ) from None
    return relative.as_posix()


def resolve_storage_path(storage_path: str) -> Path:
    """The inverse: a stored value back to a path this machine can open."""
    return REPO_ROOT / storage_path
