"""Versioned structural summary for offline tool interoperability."""

from __future__ import annotations

from hashlib import sha256

from spicetrellis._version import __version__
from spicetrellis.model import Analysis
from spicetrellis.semantics import inventory

SCHEMA = "org.spice-tools.structural-summary"


def structural_summary(analysis: Analysis) -> dict[str, object]:
    """Return a path-stable, content-bound structural summary.

    The summary carries observations only. Consumers must not interpret it as
    proof of electrical correctness or as authorization to skip parsing.
    """

    if analysis.deck is None or analysis.has_errors:
        raise ValueError("an error-free semantic analysis is required")
    root = analysis.deck.entry.parent
    snapshots = dict(analysis.source_files)
    if set(snapshots) != set(analysis.dependencies):
        raise ValueError("analysis does not contain a complete source snapshot")
    records: list[tuple[str, bytes]] = []
    logical_names: set[str] = set()
    for path in analysis.dependencies:
        resolved = path.resolve()
        try:
            logical = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("interop dependencies must be beneath the entry directory") from exc
        folded = logical.casefold()
        if folded in logical_names:
            raise ValueError("interop dependency paths must be case-insensitively unique")
        logical_names.add(folded)
        records.append((logical, snapshots[path]))
    files: list[dict[str, object]] = []
    for logical, content in sorted(records, key=lambda item: item[0].casefold()):
        files.append(
            {"path": logical, "bytes": len(content), "sha256": sha256(content).hexdigest()}
        )
    observed = inventory(analysis)
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "producer": {"name": "SpiceTrellis", "version": __version__},
        "files": files,
        "structure": {
            "files": observed.files,
            "includes": observed.includes,
            "subcircuits": len(observed.subcircuits),
            "element_families": {name.upper(): count for name, count in observed.element_families},
            "parameters": sorted({name.casefold() for name in observed.parameters}),
            "models": sorted({name.casefold() for name in observed.models}),
        },
    }
