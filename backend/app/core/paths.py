"""Where this checkout is on disk.

Separate from `config.py` because `config.py` instantiates `Settings` at import
and therefore needs a `.env`; these two constants are needed by code that has no
configuration at all, and `config.py` itself reads `BACKEND_DIR` to find the
`.env` in the first place.

Phase 1's `to_storage_path`/`resolve_storage_path` lived here. They are gone:
documents are addressed by a storage key now, not by a repo-relative path, so
there is nothing left to convert. See `app/core/storage.py`.
"""

from pathlib import Path

# app/core/paths.py -> app/core -> app -> backend.
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
