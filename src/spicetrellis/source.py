"""Physical-to-logical SPICE card normalization with source ranges."""

from __future__ import annotations

from dataclasses import dataclass

from spicetrellis.model import Diagnostic, SourceSpan


@dataclass(frozen=True, slots=True)
class LogicalCard:
    code: str
    original: str
    span: SourceSpan
    segments: tuple[SourceSpan, ...]
    kind: str = "code"
    inline_comment: str | None = None
    comment_marker: str | None = None


def _line_span(filename: str, line_number: int, text: str) -> SourceSpan:
    return SourceSpan(filename, line_number, 1, line_number, len(text) + 1)


def split_inline_comment(text: str) -> tuple[str, str | None, str | None]:
    """Split portable `$` or `;` comments outside quotes and expressions."""

    quote: str | None = None
    brace_depth = 0
    paren_depth = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth:
            paren_depth -= 1
        elif char in {"$", ";"} and brace_depth == 0 and paren_depth == 0:
            return text[:index].rstrip(), text[index + 1 :].strip(), char
    return text.rstrip(), None, None


def logical_cards(
    text: str, filename: str = "<memory>"
) -> tuple[list[LogicalCard], list[Diagnostic]]:
    """Combine leading-`+` physical continuations while retaining all spans."""

    cards: list[LogicalCard] = []
    diagnostics: list[Diagnostic] = []
    pending_code: str | None = None
    pending_original: list[str] = []
    pending_segments: list[SourceSpan] = []

    def flush() -> None:
        nonlocal pending_code, pending_original, pending_segments
        if pending_code is None:
            return
        code, comment, marker = split_inline_comment(pending_code)
        first, last = pending_segments[0], pending_segments[-1]
        cards.append(
            LogicalCard(
                code=code,
                original="\n".join(pending_original),
                span=SourceSpan(
                    filename,
                    first.start_line,
                    first.start_col,
                    last.end_line,
                    last.end_col,
                ),
                segments=tuple(pending_segments),
                inline_comment=comment,
                comment_marker=marker,
            )
        )
        pending_code = None
        pending_original = []
        pending_segments = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.lstrip()
        span = _line_span(filename, line_number, raw_line)
        if stripped.startswith("+"):
            if pending_code is None:
                diagnostics.append(
                    Diagnostic(
                        "ST1001",
                        "error",
                        "continuation card has no preceding statement",
                        span,
                    )
                )
                cards.append(
                    LogicalCard(
                        stripped[1:].strip(), raw_line, span, (span,), kind="orphan-continuation"
                    )
                )
                continue
            pending_code += " " + stripped[1:].strip()
            pending_original.append(raw_line)
            pending_segments.append(span)
            continue

        flush()
        if not stripped:
            cards.append(LogicalCard("", raw_line, span, (span,), kind="blank"))
        elif stripped.startswith("*"):
            cards.append(LogicalCard(stripped[1:].strip(), raw_line, span, (span,), kind="comment"))
        else:
            pending_code = raw_line.strip()
            pending_original = [raw_line]
            pending_segments = [span]

    flush()
    return cards, diagnostics
