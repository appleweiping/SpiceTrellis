"""Fail-closed, atomic text output shared by APIs and the CLI."""

from __future__ import annotations

import os
import tempfile
import unicodedata
from collections.abc import Iterable
from pathlib import Path


def _aliases(first: Path, second: Path) -> bool:
    try:
        first_key = unicodedata.normalize("NFC", str(first.resolve(strict=False))).casefold()
        second_key = unicodedata.normalize("NFC", str(second.resolve(strict=False))).casefold()
        if first_key == second_key:
            return True
        return first.exists() and second.exists() and first.samefile(second)
    except OSError as error:
        raise ValueError(f"cannot resolve output path alias: {error}") from error


def validate_output_paths(
    destinations: Iterable[str | Path],
    *,
    protected: Iterable[str | Path] = (),
    force: bool = False,
) -> None:
    """Validate a set before any command writes, including symlink/hardlink aliases."""

    outputs = [Path(item) for item in destinations]
    inputs = [Path(item) for item in protected]
    for index, destination in enumerate(outputs):
        if any(_aliases(destination, other) for other in outputs[:index]):
            raise ValueError(f"output path {destination} aliases another output")
        if any(_aliases(destination, source) for source in inputs):
            raise ValueError(f"output path {destination} aliases an input and is never writable")
        if destination.exists() and not force:
            raise ValueError(f"refusing to overwrite existing output {destination}; pass --force")


def write_text_atomic(
    path: str | Path,
    text: str,
    *,
    force: bool = False,
    protected: Iterable[str | Path] = (),
    prevalidated: bool = False,
) -> None:
    """Install a complete UTF-8/LF file atomically with optional replacement."""

    destination = Path(path)
    if not prevalidated:
        validate_output_paths((destination,), protected=protected, force=force)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError as error:
                raise ValueError(
                    f"refusing to overwrite existing output {destination}; pass --force"
                ) from error
        temporary.unlink(missing_ok=True)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
