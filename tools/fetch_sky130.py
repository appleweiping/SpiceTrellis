"""Download the SKY130 SPICE models used by the examples and the PDK tests.

Two pinned sources, both Apache-2.0, neither vendored here:

``--source ciel`` (default)
    The open_pdks build of ``sky130A`` that xschem and ngspice users actually
    simulate with, as released by Ciel.  About 20 MB of compressed archives;
    only ``libs.tech/ngspice`` and ``libs.ref/sky130_fd_pr/spice`` are
    unpacked.  Needs Python 3.14 or newer for ``tarfile``'s zstd support.

        python tools/fetch_sky130.py --dest .cache

``--source upstream``
    The raw ``google/skywater-pdk-libs-sky130_fd_pr`` repository at tag
    v0.20.1.  open_pdks patches several problems in these files before
    ngspice can load them; SpiceTrellis's lint finds them (see the README).
    Only the files reachable from the chosen corner are downloaded.

        python tools/fetch_sky130.py --source upstream --dest .cache/sky130_fd_pr

Files already present are not downloaded again.  Only the standard library is
used, and the include scanner below is independent of SpiceTrellis so that a
parser bug cannot hide a missing file.
"""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

UPSTREAM_REPOSITORY = "google/skywater-pdk-libs-sky130_fd_pr"
# Tag v0.20.1 of the upstream model repository.
UPSTREAM_COMMIT = "d717cd4d8d93cce6a8d7534f6a40432f0045039f"
UPSTREAM_ENTRY = "models/sky130.lib.spice"

CIEL_TAG = "sky130-1689ac3f2dc763876eaf967227c7dfe831b031ae"
CIEL_URL = f"https://github.com/fossi-foundation/ciel-releases/releases/download/{CIEL_TAG}"
CIEL_ASSETS = {
    "common.tar.zst": "92e3deed352b9a1aa53d47bc8f3de75e40dec013809c109d36ed4e8d13f74036",
    "sky130_fd_pr.tar.zst": "79ac105aa2710acf358572aa3762b065bcdd9ed408dc64f3c32d9fb71cdd287e",
}
CIEL_KEEP = ("sky130A/libs.tech/ngspice/", "sky130A/libs.ref/sky130_fd_pr/spice/")

_INCLUDE = re.compile(r"^\s*\.include\s+[\"']?([^\"'\s]+)", re.IGNORECASE)
_LIB_CALL = re.compile(r"^\s*\.lib\s+[\"']?([^\"'\s]+)[\"']?\s+(\S+)", re.IGNORECASE)
_LIB_OPEN = re.compile(r"^\s*\.lib\s+(\S+)\s*$", re.IGNORECASE)
_LIB_CLOSE = re.compile(r"^\s*\.endl\b", re.IGNORECASE)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return bytes(response.read())


# -- Ciel / open_pdks ---------------------------------------------------------


def fetch_ciel(dest: Path) -> Path:
    """Unpack the ngspice view of sky130A under ``dest`` and return the library path."""

    library = dest / "sky130A" / "libs.tech" / "ngspice" / "sky130.lib.spice"
    if library.exists():
        return library
    archives = dest / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    for name, expected in CIEL_ASSETS.items():
        archive = archives / name
        if not archive.exists():
            archive.write_bytes(_download(f"{CIEL_URL}/{name}"))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != expected:
            archive.unlink()
            raise SystemExit(f"{name}: sha256 {digest} does not match the pinned {expected}")
        if sys.version_info < (3, 14):
            raise SystemExit("unpacking .tar.zst needs Python 3.14 or newer")
        with tarfile.open(archive, "r:zst") as bundle:  # type: ignore[call-overload]
            members = [
                member
                for member in bundle.getmembers()
                if member.isfile() and member.name.startswith(CIEL_KEEP)
            ]
            bundle.extractall(dest, members=members, filter="data")
    return library


# -- raw upstream sky130_fd_pr ---------------------------------------------------


def _fetch_upstream(commit: str, path: str, dest: Path) -> str:
    target = dest / path
    if not target.exists():
        url = f"https://raw.githubusercontent.com/{UPSTREAM_REPOSITORY}/{commit}/{path}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_download(url))
    return target.read_text(encoding="utf-8", errors="replace")


def _references(text: str, section: str | None) -> list[tuple[str, str | None]]:
    """Return (path, section) pairs referenced by ``text``.

    With ``section`` set, only cards inside that ``.lib`` block are scanned;
    otherwise only cards outside every block are.
    """

    found: list[tuple[str, str | None]] = []
    current: str | None = None
    for line in text.splitlines():
        call = _LIB_CALL.match(line)
        if call:
            if current == section:
                found.append((call.group(1), call.group(2)))
            continue
        opened = _LIB_OPEN.match(line)
        if opened:
            current = opened.group(1).casefold()
            continue
        if _LIB_CLOSE.match(line):
            current = None
            continue
        include = _INCLUDE.match(line)
        if include and current == section:
            found.append((include.group(1), None))
    return found


def fetch_upstream(dest: Path, corner: str, commit: str = UPSTREAM_COMMIT) -> list[str]:
    """Download the files one corner (or ``all``) of the upstream library reaches."""

    if corner.casefold() == "all":
        text = _fetch_upstream(commit, UPSTREAM_ENTRY, dest)
        sections = sorted(
            {
                match.group(1).casefold()
                for line in text.splitlines()
                if (match := _LIB_OPEN.match(line))
            }
        )
    else:
        sections = [corner.casefold()]
    pending: list[tuple[str, str | None]] = [(UPSTREAM_ENTRY, section) for section in sections]
    seen: set[tuple[str, str | None]] = set()
    files: list[str] = []
    while pending:
        path, section = pending.pop()
        if (path, section) in seen:
            continue
        seen.add((path, section))
        text = _fetch_upstream(commit, path, dest)
        if path not in files:
            files.append(path)
        base = posixpath.dirname(path)
        for reference, nested in _references(text, section):
            resolved = posixpath.normpath(posixpath.join(base, reference))
            pending.append((resolved, nested.casefold() if nested else None))
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--source", choices=("ciel", "upstream"), default="ciel")
    parser.add_argument("--corner", default="tt", help="upstream only: a corner name or 'all'")
    args = parser.parse_args(argv)
    if args.source == "ciel":
        print(f"library: {fetch_ciel(args.dest).resolve()}")
        return 0
    files = fetch_upstream(args.dest, args.corner)
    total = sum((args.dest / item).stat().st_size for item in files)
    print(f"{len(files)} files, {total / 1e6:.1f} MB, corner {args.corner}")
    print(f"library: {(args.dest / UPSTREAM_ENTRY).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
