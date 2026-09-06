"""Immutable domain objects shared by the SpiceTrellis pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from spicetrellis.expressions import Expr


@dataclass(frozen=True, slots=True, order=True)
class SourceSpan:
    """A one-based source range."""

    filename: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.filename,
            "start": [self.start_line, self.start_col],
            "end": [self.end_line, self.end_col],
        }


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A stable, source-addressed parser or semantic diagnostic."""

    code: str
    severity: str
    message: str
    primary: SourceSpan
    related: tuple[SourceSpan, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "primary": self.primary.as_dict(),
            "related": [span.as_dict() for span in self.related],
        }


@dataclass(frozen=True, slots=True)
class CardMeta:
    """Source and trivia attached to one logical SPICE card."""

    span: SourceSpan
    segments: tuple[SourceSpan, ...]
    raw: str
    inline_comment: str | None = None
    comment_marker: str | None = None


@dataclass(frozen=True, slots=True)
class Assignment:
    name: str
    expression: Expr
    raw: str


@dataclass(frozen=True, slots=True)
class Blank:
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Comment:
    text: str
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Include:
    target: str
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class LibCall:
    """A request for one named section of a library file.

    This is the two-argument ``.lib PATH SECTION`` form, which is how a deck
    selects a PDK corner. The one-argument form is deliberately not accepted:
    dialects disagree about whether it includes a whole file or opens a section,
    and guessing would silently change which device models a circuit is built
    from. Use ``.include`` for a whole file.
    """

    target: str
    section: str
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class LibSectionStart:
    """The opening of a ``.lib SECTION`` block inside a library file."""

    name: str
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class LibSectionEnd:
    """A ``.endl`` card, optionally naming the section it closes."""

    name: str | None
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Param:
    assignments: tuple[Assignment, ...]
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class SubcktStart:
    name: str
    pins: tuple[str, ...]
    defaults: tuple[Assignment, ...]
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class SubcktEnd:
    name: str | None
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Model:
    name: str
    kind: str
    tail: str
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Global:
    nodes: tuple[str, ...]
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class End:
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Element:
    """A supported primitive or subcircuit instance."""

    name: str
    family: str
    nodes: tuple[str, ...]
    tail: str
    parameters: tuple[Assignment, ...]
    value: Expr | None
    model: str | None
    meta: CardMeta


@dataclass(frozen=True, slots=True)
class Opaque:
    text: str
    reason: str
    meta: CardMeta


Statement: TypeAlias = (
    Blank
    | Comment
    | Include
    | LibCall
    | LibSectionStart
    | LibSectionEnd
    | Param
    | SubcktStart
    | SubcktEnd
    | Model
    | Global
    | End
    | Element
    | Opaque
)


@dataclass(frozen=True, slots=True)
class SyntaxDeck:
    filename: str
    statements: tuple[Statement, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class Subcircuit:
    name: str
    pins: tuple[str, ...]
    defaults: tuple[Assignment, ...]
    body: tuple[Statement, ...]
    span: SourceSpan


@dataclass(frozen=True, slots=True)
class SemanticDeck:
    entry: Path
    files: tuple[SyntaxDeck, ...]
    top: tuple[Statement, ...]
    subcircuits: tuple[Subcircuit, ...]
    global_parameters: tuple[Assignment, ...]
    global_nodes: tuple[str, ...]
    dependencies: tuple[Path, ...]

    def subcircuit_map(self) -> dict[str, Subcircuit]:
        return {subckt.name.casefold(): subckt for subckt in self.subcircuits}


@dataclass(frozen=True, slots=True)
class Analysis:
    deck: SemanticDeck | None
    diagnostics: tuple[Diagnostic, ...]
    dependencies: tuple[Path, ...]
    source_files: tuple[tuple[Path, bytes], ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)


@dataclass(frozen=True, slots=True)
class Provenance:
    output_index: int
    output_name: str
    definition: SourceSpan
    expansion_chain: tuple[SourceSpan, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "output_index": self.output_index,
            "output_name": self.output_name,
            "definition": self.definition.as_dict(),
            "expansion_chain": [span.as_dict() for span in self.expansion_chain],
        }


@dataclass(frozen=True, slots=True)
class ElaboratedDeck:
    statements: tuple[Statement, ...]
    provenance: tuple[Provenance, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)


@dataclass(frozen=True, slots=True)
class CircuitInventory:
    files: int
    includes: int
    library_sections: int
    subcircuits: tuple[dict[str, Any], ...]
    element_families: tuple[tuple[str, int], ...]
    parameters: tuple[str, ...]
    models: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "files": self.files,
            "includes": self.includes,
            "library_sections": self.library_sections,
            "subcircuits": list(self.subcircuits),
            "element_families": dict(self.element_families),
            "parameters": list(self.parameters),
            "models": list(self.models),
        }


def sorted_diagnostics(items: list[Diagnostic] | tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    """Return diagnostics in a byte-stable presentation order."""

    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.primary.filename,
                item.primary.start_line,
                item.primary.start_col,
                0 if item.severity == "error" else 1,
                item.code,
                item.message,
            ),
        )
    )
