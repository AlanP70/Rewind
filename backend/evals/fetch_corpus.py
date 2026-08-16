"""Fetch the eval corpus, and refuse anything that is not what was measured.

The 20 MIT 6.006 lecture PDFs are not committed -- they are 4.8 MB of somebody
else's material, and a repository is the wrong place to keep a copy of a file
that already has a canonical home. What is committed is `corpus.tsv`: the exact
URL and the exact `sha256` of every file the eval numbers were measured against.

**The checksum is the whole point of this script.** Reproducibility matters more
for a measurement than for the bytes themselves. If MIT re-renders lecture 7 next
year, an unpinned download would hand the eval a different document, the score
would move, and the move would look like a retrieval regression -- a wrong number
arriving with no error attached to it. Here it stops the fetch and says which
file changed.

Standard library only. This is not part of the application: nothing under `app/`
imports it, and it must run before there is a database to talk to.

    python evals/fetch_corpus.py            # into evals/corpus/
    python evals/fetch_corpus.py --check    # verify what is already there
"""

import argparse
import hashlib
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
MANIFEST = HERE / "corpus.tsv"
CORPUS = HERE / "corpus"

# OCW serves the PDFs to a plain client, but a default urllib agent string is the
# kind of thing a CDN starts refusing without warning; naming the caller is
# cheaper than debugging a 403 later.
HEADERS = {"User-Agent": "rewind-eval-corpus/1.0 (+https://github.com/AlanP70/Rewind)"}


def entries() -> list[tuple[str, str, int, str]]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, digest, size, url = line.split("\t")
        rows.append((name, digest, int(size), url))
    return rows


def digest_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the files already present and download nothing",
    )
    args = parser.parse_args()

    CORPUS.mkdir(parents=True, exist_ok=True)
    have, fetched, missing, corrupt = 0, 0, [], []

    for name, expected, size, url in entries():
        path = CORPUS / name

        # An existing file with the right hash is already the file the eval was
        # measured against, so there is nothing a download could improve.
        if path.exists() and digest_of(path) == expected:
            have += 1
            continue

        if args.check:
            (corrupt if path.exists() else missing).append(name)
            continue

        try:
            body = fetch(url)
        except urllib.error.URLError as error:
            print(f"  !! {name}: {error}", file=sys.stderr)
            missing.append(name)
            continue

        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            # Deliberately not written to disk. A file that is on disk gets used
            # by the next command someone runs, and the point of this check is
            # that unmeasured bytes never reach the eval.
            print(
                f"  !! {name}: upstream file has changed\n"
                f"     expected sha256 {expected} ({size} bytes)\n"
                f"     got      sha256 {actual} ({len(body)} bytes)\n"
                f"     {url}\n"
                f"     Not saved. Re-measure the eval against the new file and\n"
                f"     update corpus.tsv deliberately -- do not edit the hash to\n"
                f"     make this pass.",
                file=sys.stderr,
            )
            corrupt.append(name)
            continue

        path.write_bytes(body)
        fetched += 1
        print(f"  {name} ({len(body)} bytes)")

    total = len(entries())
    print(f"\n{have} already present, {fetched} fetched, of {total} in {CORPUS}")
    if missing:
        print(f"missing: {', '.join(missing)}", file=sys.stderr)
    if corrupt:
        print(f"checksum mismatch: {', '.join(corrupt)}", file=sys.stderr)
    return 1 if (missing or corrupt) else 0


if __name__ == "__main__":
    raise SystemExit(main())
