"""The local storage backend and the key format.

`SupabaseStorage` is not tested here and deliberately so: a test that needs
credentials cannot run on a clean clone, which is the bar Phase 0 set and the
reason there are two backends at all. It was instead exercised by hand against
the real bucket when this slice landed -- upload, upsert, download, and a missing
key -- and that is recorded in ROADMAP.
"""

import uuid

import pytest

from app.core.storage import LocalStorage, get_storage, storage_key
from app.services.errors import ServiceError

USER = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def test_key_is_user_scoped_and_keeps_the_filename() -> None:
    assert storage_key(USER, "Lecture 07.pdf") == f"{USER}/Lecture 07.pdf"


@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd", r"C:\Users\Alan\lecture.pdf", "nested/dir/lecture.pdf"],
)
def test_a_key_cannot_escape_its_user_prefix(filename: str) -> None:
    """From slice 3 this filename comes from an HTTP client.

    A key with a directory component in it would write outside the user's prefix
    -- and on the local backend, outside the storage root entirely.
    """
    key = storage_key(USER, filename)
    assert key.startswith(f"{USER}/")
    assert key.count("/") == 1
    assert ".." not in key


def test_a_filename_that_is_only_a_path_is_refused() -> None:
    with pytest.raises(ServiceError, match="no usable filename"):
        storage_key(USER, "../")


async def test_upload_then_download_returns_the_same_bytes(storage: LocalStorage) -> None:
    key = storage_key(USER, "lecture.pdf")
    await storage.upload(key, b"\x00\x01binary\xff")

    assert await storage.download(key) == b"\x00\x01binary\xff"


async def test_upload_overwrites_rather_than_refusing(storage: LocalStorage) -> None:
    """Re-ingesting a corrected lecture is the same key by design, so a second
    upload has to replace the first. The Supabase backend gets this from
    `x-upsert`; here it is what writing a file already does."""
    key = storage_key(USER, "lecture.pdf")
    await storage.upload(key, b"first")
    await storage.upload(key, b"second")

    assert await storage.download(key) == b"second"


async def test_a_missing_key_is_a_service_error(storage: LocalStorage) -> None:
    """`ServiceError` specifically, because that is what marks a failure
    permanent. An object that is not there will not be there on attempt 3, and
    retrying three times would make a settled fact look like flakiness."""
    with pytest.raises(ServiceError, match="missing"):
        await storage.download(storage_key(USER, "never-uploaded.pdf"))


def test_the_suite_never_reaches_the_real_bucket() -> None:
    """The autouse fixture is load-bearing, so it gets an assertion of its own.

    If `get_storage()` ever returns the Supabase backend in a test run, every
    other test in this file is quietly writing to production.
    """
    assert isinstance(get_storage(), LocalStorage)
