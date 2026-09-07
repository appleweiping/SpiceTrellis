"""Asking the source map questions, in both directions.

Flattening writes a source map recording, for every card it produced, the
definition it came from and every instance site it was expanded through. That
is the point of the front end. It was also, until now, only a JSON file: a
caller who wanted to use it had to load it and search it themselves, which is
the work the map was supposed to save.

Two questions are worth asking, and they run opposite ways.

Backwards, from output to source: a simulator failed on a card and the reader
needs the line that produced it. The answer is not one line but a chain -- the
definition inside a subcircuit, plus every `X` instance the expansion passed
through on the way to this copy. A subcircuit instantiated four times has four
cards from one definition, and the definition alone does not say which one
failed.

Forwards, from source to output: a line is about to be edited and the reader
needs to know what it becomes. One line inside a twice-instantiated subcircuit
becomes two cards, and a line inside a subcircuit nothing instantiates becomes
none. The second case is the interesting one, and a forward query is the only
thing that shows it.

Both are lookups over the same map, built once. The index is deliberately not
part of the elaborated deck: flattening should not pay for a question nobody
asked, and a deck read back from JSON should be able to answer the same
questions as one just produced.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from spicetrellis.model import ElaboratedDeck, Provenance, SourceSpan

#: Separator between instance names in a generated flat name. Splitting on it
#: is exact rather than heuristic: the elaborator encodes every segment so that
#: `__` cannot occur inside one, turning a literal underscore into `_u` and any
#: other byte into `_xNN`. A path parsed here is the path the elaborator built.
PATH_SEPARATOR = "__"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where one flattened card came from, and how it got there."""

    output_index: int
    output_name: str
    definition: SourceSpan
    expansion_chain: tuple[SourceSpan, ...]

    @property
    def instance_path(self) -> tuple[str, ...]:
        """Instance names the flat name encodes, outermost first.

        The final segment is the card's own name inside its definition, so a
        top-level card has an empty path rather than a path of one.
        """

        parts = self.output_name.split(PATH_SEPARATOR)
        return tuple(parts[:-1])

    @property
    def local_name(self) -> str:
        return self.output_name.split(PATH_SEPARATOR)[-1]

    @property
    def depth(self) -> int:
        return len(self.expansion_chain)

    def as_dict(self) -> dict[str, Any]:
        return {
            "output_index": self.output_index,
            "output_name": self.output_name,
            "local_name": self.local_name,
            "instance_path": list(self.instance_path),
            "depth": self.depth,
            "definition": self.definition.as_dict(),
            "expansion_chain": [span.as_dict() for span in self.expansion_chain],
        }

    def describe(self) -> str:
        """Render the chain the way a reader follows it: outermost site first."""

        lines = [f"{self.output_name} (card {self.output_index})"]
        for depth, span in enumerate(self.expansion_chain):
            lines.append(
                f"  {'  ' * depth}expanded at {span.filename}:{span.start_line}:{span.start_col}"
            )
        lines.append(
            f"  {'  ' * self.depth}defined at {self.definition.filename}:"
            f"{self.definition.start_line}:{self.definition.start_col}"
        )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class SourceUse:
    """One source line and every flattened card it produced."""

    filename: str
    line: int
    origins: tuple[Origin, ...]

    @property
    def copies(self) -> int:
        return len(self.origins)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.filename,
            "line": self.line,
            "copies": self.copies,
            "origins": [origin.as_dict() for origin in self.origins],
        }


class ProvenanceIndex:
    """Lookups over one flattening's source map."""

    def __init__(self, entries: Iterable[Provenance]) -> None:
        self._origins = tuple(
            Origin(
                output_index=entry.output_index,
                output_name=entry.output_name,
                definition=entry.definition,
                expansion_chain=tuple(entry.expansion_chain),
            )
            for entry in entries
        )
        self._by_name = {origin.output_name: origin for origin in self._origins}
        self._by_index = {origin.output_index: origin for origin in self._origins}
        self._by_definition: dict[tuple[str, int], list[Origin]] = defaultdict(list)
        for origin in self._origins:
            # A definition can span continued lines, and a reader pointing at
            # any of them means the same statement, so every covered line maps
            # to the card.
            span = origin.definition
            for line in range(span.start_line, span.end_line + 1):
                self._by_definition[(span.filename, line)].append(origin)

    @property
    def origins(self) -> tuple[Origin, ...]:
        return self._origins

    def by_name(self, name: str) -> Origin | None:
        """Find the card with this flat name."""

        return self._by_name.get(name)

    def by_index(self, index: int) -> Origin | None:
        """Find the card at this position in the flattened deck."""

        return self._by_index.get(index)

    def by_source(self, filename: str, line: int) -> SourceUse:
        """Every card this source line produced.

        An empty result is a real answer: a line inside a subcircuit nothing
        instantiates produces no card, and that is worth being able to see.
        """

        found = self._by_definition.get((filename, line), [])
        return SourceUse(
            filename=filename,
            line=line,
            origins=tuple(sorted(found, key=lambda origin: origin.output_index)),
        )

    def under(self, instance_path: Sequence[str]) -> tuple[Origin, ...]:
        """Every card produced beneath one instance path.

        Answers what a whole instance expanded into, which is the question
        after a simulator blames a subcircuit rather than a single card.
        """

        prefix = tuple(instance_path)
        return tuple(
            origin for origin in self._origins if origin.instance_path[: len(prefix)] == prefix
        )

    def resolve(self, query: str) -> Origin | None:
        """Look a card up by flat name, or by output index written as digits.

        A deck can legitimately contain a card named `12`, so the name is tried
        first and the index only when no name matches. Guessing the other way
        round would make a lookup depend on what else is in the deck.
        """

        found = self.by_name(query)
        if found is not None:
            return found
        if query.isdigit():
            return self.by_index(int(query))
        return None


def build_index(deck: ElaboratedDeck) -> ProvenanceIndex:
    """Index the source map a flattening produced."""

    return ProvenanceIndex(deck.provenance)


def index_from_entries(entries: Iterable[Any]) -> ProvenanceIndex:
    """Index a source map read back from JSON.

    A map that has been written out and read back should answer the same
    questions as one still in memory, so the entries are accepted as plain
    mappings and rebuilt rather than requiring the original objects.
    """

    return ProvenanceIndex(_provenance_from(entry) for entry in entries)


def _span_from(value: Any, context: str) -> SourceSpan:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    filename = value.get("file")
    start = value.get("start")
    end = value.get("end")
    if not isinstance(filename, str):
        raise ValueError(f"{context} file must be a string")
    for label, pair in (("start", start), ("end", end)):
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in pair)
        ):
            raise ValueError(f"{context} {label} must be two integers")
    assert isinstance(start, list) and isinstance(end, list)
    return SourceSpan(filename, start[0], start[1], end[0], end[1])


def _provenance_from(value: Any) -> Provenance:
    if not isinstance(value, dict):
        raise ValueError("source-map entry must be an object")
    index = value.get("output_index")
    name = value.get("output_name")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("source-map entry output_index must be an integer")
    if not isinstance(name, str):
        raise ValueError("source-map entry output_name must be a string")
    chain = value.get("expansion_chain", [])
    if not isinstance(chain, list):
        raise ValueError("source-map entry expansion_chain must be a list")
    return Provenance(
        output_index=index,
        output_name=name,
        definition=_span_from(value.get("definition"), "definition"),
        expansion_chain=tuple(
            _span_from(item, f"expansion_chain[{position}]") for position, item in enumerate(chain)
        ),
    )
