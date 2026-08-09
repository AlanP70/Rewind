"""Where document bytes live, and how `documents.storage_key` addresses them.

Two backends, chosen by `STORAGE_BACKEND`, both real. `local` writes under
`backend/.storage` and is what a fresh clone runs on -- a repo that needs
credentials before it can run its own `verify` fails the bar Phase 0 set.
`supabase` is what the deploy runs on, because the upload endpoint and the worker
are separate Render services with no shared disk, so a path into local disk would
work on no machine but the developer's.

Neither is a stub for the other. Two implementations from the first commit is not
speculative abstraction when both are used.

**A missing object raises `ServiceError`; a server or network failure does not.**
That split is the same one `extraction.py` makes, for the same consumer: the
retry classifier in `processing.py` sees an exception type and nothing else. An
object that is not there will still not be there on attempt 3, and a bucket that
rejects a 60MB file or a non-PDF rejects it identically every time -- permanent.
A 5xx or a dropped connection is transient and keeps its own exception type so it
retries.
"""

import uuid
from pathlib import Path, PurePosixPath
from typing import Protocol

import httpx

from app.core.config import settings
from app.services.errors import ServiceError


def storage_key(user_id: uuid.UUID, filename: str) -> str:
    """`{user_id}/{filename}` -- the value `documents.storage_key` stores.

    Deliberately not content-addressed. Under a content hash, re-exporting a
    lecture with one slide changed produces a different key and therefore a
    second document, silently orphaning the first: the concept timeline that
    should span a semester splits in two and nothing errors. Keying on the
    filename means the same lecture is the same document, which is what
    `UNIQUE (user_id, storage_key)` and `--force` already assume.

    The consequence accepted with it: two different files with the same name are
    one key. That collides loudly -- the second ingest reports the document
    already exists -- rather than quietly.
    """
    # Any directory component is stripped, both separators, on every platform.
    # From slice 3 this filename arrives from an HTTP client, where `../` would
    # otherwise escape the user's prefix -- and on the local backend, escape
    # `.storage` entirely.
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise ServiceError(f"{filename!r} has no usable filename")
    return f"{user_id}/{name}"


class Storage(Protocol):
    """Two methods. There is no `exists` because `download` raising is the
    answer, and no `delete` because nothing in Phase 2 deletes."""

    async def upload(self, key: str, data: bytes) -> None: ...

    async def download(self, key: str) -> bytes: ...


class LocalStorage:
    """Files under a root directory, one directory per user.

    Blocking file IO inside `async def`: this backend is development only and the
    payload is one PDF, so a thread pool would cost more to explain than the
    blocked event loop costs to run. The methods are async because the protocol
    is, and the protocol is async because Supabase is over the network.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    async def upload(self, key: str, data: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def download(self, key: str) -> bytes:
        path = self.root / key
        if not path.is_file():
            raise ServiceError(f"{key} is recorded but missing from {self.root}")
        return path.read_bytes()


class SupabaseStorage:
    """Supabase Storage over its REST API.

    Two endpoints -- upload and download -- which is why this calls them directly
    instead of adding the `supabase` client to the tree for them. `httpx` is
    already present underneath `openai`; it is declared as a direct dependency
    rather than leaned on transitively.
    """

    def __init__(self, url: str, service_key: str, bucket: str) -> None:
        self.base = f"{url.rstrip('/')}/storage/v1/object/{bucket}"
        # Both headers, because the two key formats are authenticated by
        # different things. A legacy `service_role` key is a JWT that storage-api
        # validates from `Authorization` itself. A current `sb_secret_...` key is
        # not a JWT at all: the API gateway resolves it from `apikey` and mints
        # the internal token. Sending only `Authorization` with one of those
        # fails as `Invalid Compact JWS` -- a message about token *shape*, which
        # reads like a corrupted secret rather than a missing header.
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    @staticmethod
    def _check(response: httpx.Response, action: str, key: str) -> None:
        """Translate 4xx to `ServiceError`; let everything else keep its type.

        See the module docstring. A 404 for a key that was never uploaded, a 400
        for a file the bucket's MIME restriction rejects, a 413 for one over its
        size cap -- all deterministic, all permanent. `raise_for_status` handles
        the 5xx case, where retrying is exactly right.
        """
        if 400 <= response.status_code < 500:
            raise ServiceError(
                f"could not {action} {key}: Supabase returned "
                f"{response.status_code} {response.text[:200]}"
            )
        response.raise_for_status()

    async def upload(self, key: str, data: bytes) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base}/{key}",
                content=data,
                headers={
                    **self.headers,
                    "Content-Type": "application/pdf",
                    # Re-ingesting a corrected lecture is the same key by design.
                    # Without upsert that is a 409, and `--force` would then be
                    # refused for a reason with nothing to do with chunks.
                    "x-upsert": "true",
                },
            )
        self._check(response, "upload", key)

    async def download(self, key: str) -> bytes:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{self.base}/{key}", headers=self.headers)
        self._check(response, "download", key)
        return response.content


def get_storage() -> Storage:
    """The backend `STORAGE_BACKEND` selects.

    Constructed per call rather than once at import, unlike `core/redis.py`: the
    tests point the local root at a `tmp_path` by setting the setting, and a
    module-level instance would have frozen the old root before they got there.
    """
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_root)

    url = settings.supabase_url
    service_key = settings.supabase_service_key

    # Named here rather than left to fail inside httpx as a confusing 401, the
    # same courtesy `openai_api_key` gets.
    missing = [
        name
        for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_KEY", service_key))
        if not value
    ]
    if missing:
        raise ServiceError(
            f"STORAGE_BACKEND=supabase needs {' and '.join(missing)} set in backend/.env"
        )

    return SupabaseStorage(url=url, service_key=service_key, bucket=settings.storage_bucket)
