"""Card-level parser for SPICE decks as ngspice reads them."""

from __future__ import annotations

import re
from functools import lru_cache

from spicetrellis.expressions import (
    Expr,
    ExpressionError,
    ExpressionLimitError,
    parse_expression,
    parse_number,
)
from spicetrellis.model import (
    Assignment,
    Blank,
    CardMeta,
    Comment,
    Control,
    Diagnostic,
    Directive,
    Element,
    End,
    Func,
    Global,
    Include,
    LibCall,
    LibSectionEnd,
    LibSectionStart,
    Model,
    Opaque,
    Option,
    Param,
    SourceSpan,
    Statement,
    SubcktEnd,
    SubcktStart,
    SyntaxDeck,
    Title,
    sorted_diagnostics,
)
from spicetrellis.source import LogicalCard, logical_cards

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.$:-]*\Z")
_GROUPING = re.compile(r"[\"'{}()\\]")
_PIECES = re.compile(r"[^\s\"'{}()\\]+|\s+|[\s\S]")
_ASSIGNMENT_START = re.compile(r"[A-Za-z_][A-Za-z0-9_.$:-]*=(?!=)")
_MODEL_CARD = re.compile(r"\.model\s+(\S+)\s+([A-Za-z][A-Za-z0-9_]*)\s*(.*)\Z", re.I | re.S)
_FUNC_CARD = re.compile(
    r"\.func\s+([A-Za-z_][A-Za-z0-9_.$]*)\s*\(([^()]*)\)\s*=?\s*(.+)\Z", re.I | re.S
)
_POLY = re.compile(r"poly\((\d+)\)\Z", re.I)

# Cards that run or report a simulation but do not change the circuit.
DIRECTIVES = frozenset(
    {
        ".ac",
        ".csparam",
        ".dc",
        ".disto",
        ".four",
        ".ic",
        ".meas",
        ".measure",
        ".noise",
        ".nodeset",
        ".op",
        ".pss",
        ".plot",
        ".print",
        ".probe",
        ".pz",
        ".save",
        ".sens",
        ".sp",
        ".temp",
        ".tf",
        ".title",
        ".tran",
        ".width",
    }
)
_OPTION_KEYWORDS = frozenset({".option", ".options", ".opt"})
_INCLUDE_KEYWORDS = frozenset({".include", ".inc"})

# family -> (minimum nodes, maximum nodes) for "nodes, model, [area]" devices.
_MODEL_DEVICES = {
    "D": (2, 2),
    "Q": (3, 5),
    "J": (3, 3),
    "Z": (3, 3),
    "M": (4, 7),
    "O": (4, 4),
    "U": (3, 3),
}
SUPPORTED_FAMILIES = frozenset("RCLVIDQJZMEFGHBKSWTOUX")


class CardParseError(ValueError):
    pass


def split_fields(text: str) -> list[str]:
    """Split whitespace-delimited fields while respecting quotes/braces/parentheses."""

    if not _GROUPING.search(text):
        return text.split()
    fields: list[str] = []
    start: int | None = None
    quote: str | None = None
    escaped = False
    braces = 0
    parentheses = 0
    # Runs of ordinary characters are one step each; only the grouping
    # characters and whitespace need to be looked at one by one.
    for piece in _PIECES.finditer(text):
        index = piece.start()
        char = piece.group(0)
        if escaped:
            escaped = False
            continue
        if quote and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            if start is None:
                start = index
        elif char == "{":
            braces += 1
            if start is None:
                start = index
        elif char == "}":
            braces -= 1
            if braces < 0:
                raise CardParseError("unmatched closing brace")
        elif char == "(":
            parentheses += 1
            if start is None:
                start = index
        elif char == ")":
            parentheses -= 1
            if parentheses < 0:
                raise CardParseError("unmatched closing parenthesis")
        elif char.isspace() and braces == 0 and parentheses == 0:
            if start is not None:
                fields.append(text[start:index])
                start = None
        elif start is None:
            start = index
    if quote:
        raise CardParseError("unterminated quoted string")
    if braces:
        raise CardParseError("unbalanced braces")
    if parentheses:
        raise CardParseError("unbalanced parentheses")
    if start is not None:
        fields.append(text[start:])
    return fields


def _meta(card: LogicalCard) -> CardMeta:
    return CardMeta(
        card.span,
        card.segments,
        card.original,
        card.inline_comment,
        card.comment_marker,
    )


def _is_assignment_at(fields: list[str], index: int) -> bool:
    field = fields[index]
    if _ASSIGNMENT_START.match(field):
        return True
    if not _IDENTIFIER.fullmatch(field) or index + 1 >= len(fields):
        return False
    following = fields[index + 1]
    return following.startswith("=") and not following.startswith("==")


def _first_assignment_index(fields: list[str]) -> int:
    for index in range(len(fields)):
        if _is_assignment_at(fields, index):
            return index
    return len(fields)


def _assignments(fields: list[str]) -> tuple[Assignment, ...]:
    """Read ``name=value`` pairs, tolerating spaces around ``=``.

    A value runs until the next ``name=``, so an unbraced behavioral expression
    such as ``V = v(a) * 2`` is read whole.
    """

    result: list[Assignment] = []
    index = 0
    while index < len(fields):
        if not _is_assignment_at(fields, index):
            raise CardParseError(f"expected name=value assignment, got {fields[index]!r}")
        field = fields[index]
        if "=" in field and _ASSIGNMENT_START.match(field):
            name, raw = field.split("=", 1)
            index += 1
        else:
            name = field
            raw = fields[index + 1][1:]
            index += 2
        pieces = [raw] if raw else []
        while index < len(fields) and not _is_assignment_at(fields, index):
            pieces.append(fields[index])
            index += 1
        value = " ".join(pieces)
        if not value:
            raise CardParseError(f"assignment {name!r} has no value")
        if not _IDENTIFIER.fullmatch(name):
            raise CardParseError(f"invalid parameter name {name!r}")
        try:
            expression = parse_expression(value)
        except ExpressionError as error:
            raise CardParseError(f"invalid expression for {name}: {error}") from error
        result.append(Assignment(name, expression, value))
    return tuple(result)


def _split_instance(fields: list[str]) -> tuple[list[str], tuple[Assignment, ...]]:
    """Split an X line: one-token ``name=value`` pairs anywhere, everything else positional.

    ngspice takes the subcircuit name as the last field that is not a
    parameter, and SKY130's specialized cells put it after the parameters.
    """

    positional: list[str] = []
    pairs: list[str] = []
    index = 0
    while index < len(fields):
        if not _is_assignment_at(fields, index):
            positional.append(fields[index])
            index += 1
            continue
        field = fields[index]
        if _ASSIGNMENT_START.match(field):
            name, raw = field.split("=", 1)
            index += 1
        else:
            name, raw = field, fields[index + 1][1:]
            index += 2
        if not raw:
            if index >= len(fields):
                raise CardParseError(f"assignment {name!r} has no value")
            raw = fields[index]
            index += 1
        pairs.append(f"{name}={raw}")
    return positional, _assignments(pairs)


def _drop_params_keyword(fields: list[str]) -> list[str]:
    return [field for field in fields if field.casefold() != "params:"]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _looks_like_value(field: str) -> bool:
    """True for a number or a delimited expression, false for a bare name."""

    return (
        field[:1] in {"{", "'", '"', "(", "+", "-", "."}
        or field[:1].isdigit()
        or parse_number(field) is not None
    )


def _expression(raw: str, what: str) -> Expr:
    try:
        return parse_expression(raw)
    except ExpressionError as error:
        raise CardParseError(f"invalid {what}: {error}") from error


def _split(fields: list[str]) -> tuple[list[str], tuple[Assignment, ...]]:
    boundary = _first_assignment_index(fields)
    return fields[:boundary], _assignments(fields[boundary:])


def _passive(fields: list[str], meta: CardMeta) -> Element:
    name, family = fields[0], fields[0][0].upper()
    if len(fields) < 3:
        raise CardParseError(f"{family} element requires name, two nodes, and value")
    positional, parameters = _split(fields[3:])
    value = None
    model = None
    if len(positional) == 1:
        if _looks_like_value(positional[0]):
            value = _expression(positional[0], f"{family} value")
        else:
            model = positional[0]
    elif len(positional) == 2:
        value = _expression(positional[0], f"{family} value")
        model = positional[1]
    elif positional:
        raise CardParseError(f"{family} element has unexpected fields {' '.join(positional[2:])}")
    named = {item.name.casefold() for item in parameters}
    # ngspice also takes a charge (C q=...) or flux (L flux=...) expression.
    value_names = {"R": {"r"}, "C": {"c", "q"}, "L": {"l", "flux"}}[family]
    if value is None and model is None and not named & value_names:
        raise CardParseError(f"{family} element requires name, two nodes, and value")
    return Element(name, family, tuple(fields[1:3]), "", parameters, value, model, meta)


def _model_device(fields: list[str], meta: CardMeta) -> Element:
    name, family = fields[0], fields[0][0].upper()
    low, high = _MODEL_DEVICES[family]
    positional, parameters = _split(fields[1:])
    # The model is the last positional field that is not a number; anything after
    # it is the area factor (or an initial "off").
    model_index = None
    for index in range(len(positional) - 1, -1, -1):
        if not _looks_like_value(positional[index]) and positional[index].casefold() != "off":
            model_index = index
            break
    if model_index is None or not low <= model_index <= high:
        noun = "MOS element" if family == "M" else f"{family} element"
        raise CardParseError(
            f"{noun} requires name, {low}"
            + (f"-{high}" if high != low else "")
            + " nodes, and model"
        )
    extra = positional[model_index + 1 :]
    value = None
    tail = ""
    if extra and extra[0].casefold() != "off":
        value = _expression(extra[0], "area")
        extra = extra[1:]
    if extra:
        if [item.casefold() for item in extra] != ["off"]:
            raise CardParseError(f"{family} element has unexpected fields {' '.join(extra)}")
        tail = "off"
    return Element(
        name,
        family,
        tuple(positional[:model_index]),
        tail,
        parameters,
        value,
        positional[model_index],
        meta,
    )


def _controlled(fields: list[str], meta: CardMeta) -> Element:
    """E/G (voltage-controlled) and F/H (current-controlled) sources."""

    name, family = fields[0], fields[0][0].upper()
    if len(fields) < 4:
        raise CardParseError(f"{family} source requires two nodes and a control")
    nodes = tuple(fields[1:3])
    head = fields[3]
    poly = _POLY.fullmatch(head)
    if poly:
        count = int(poly.group(1))
        if not 1 <= count <= 32:
            raise CardParseError("poly() order must be between 1 and 32")
        width = 2 * count if family in {"E", "G"} else count
        controls = fields[4 : 4 + width]
        if len(controls) != width:
            raise CardParseError(f"poly({count}) needs {width} controlling names")
        coefficients = fields[4 + width :]
        if not coefficients:
            raise CardParseError("poly() source has no coefficients")
        if family in {"E", "G"}:
            return Element(
                name,
                family,
                (*nodes, *controls),
                " ".join(coefficients),
                (),
                None,
                None,
                meta,
                form=f"poly({count})",
            )
        return Element(
            name,
            family,
            nodes,
            " ".join(coefficients),
            (),
            None,
            None,
            meta,
            controls=tuple(controls),
            form=f"poly({count})",
        )
    if family in {"E", "G"} and _is_assignment_at(fields, 3):
        parameters = _assignments(fields[3:])
        keys = {item.name.casefold() for item in parameters}
        if not keys & {"value", "vol", "cur"}:
            raise CardParseError(f"behavioral {family} source needs value=, vol= or cur=")
        return Element(name, family, nodes, "", parameters, None, None, meta, form="behavioral")
    if head.casefold() in {"value", "table", "laplace", "freq", "vol", "cur"}:
        raise CardParseError(f"{family} source form {head!r} is not supported")
    if family in {"E", "G"}:
        positional, parameters = _split(fields[3:])
        if len(positional) != 3:
            raise CardParseError(f"{family} source requires four nodes and a gain")
        return Element(
            name,
            family,
            (*nodes, *positional[:2]),
            "",
            parameters,
            _expression(positional[2], "gain"),
            None,
            meta,
        )
    positional, parameters = _split(fields[3:])
    if len(positional) != 2:
        raise CardParseError(f"{family} source requires two nodes, a voltage source and a gain")
    return Element(
        name,
        family,
        nodes,
        "",
        parameters,
        _expression(positional[1], "gain"),
        None,
        meta,
        controls=(positional[0],),
    )


def _parse_element(fields: list[str], meta: CardMeta) -> Element | Opaque:
    name = fields[0]
    family = name[0].upper()
    if family in {"R", "C", "L"}:
        return _passive(fields, meta)
    if family in {"V", "I"}:
        if len(fields) < 4:
            raise CardParseError(f"{family} source requires name, two nodes, and source value")
        return Element(name, family, tuple(fields[1:3]), " ".join(fields[3:]), (), None, None, meta)
    if family in _MODEL_DEVICES:
        return _model_device(fields, meta)
    if family in {"E", "F", "G", "H"}:
        return _controlled(fields, meta)
    if family == "B":
        if len(fields) < 4:
            raise CardParseError("B source requires two nodes and v= or i=")
        parameters = _assignments(fields[3:])
        if not {item.name.casefold() for item in parameters} & {"v", "i"}:
            raise CardParseError("B source requires v= or i=")
        return Element(name, family, tuple(fields[1:3]), "", parameters, None, None, meta)
    if family == "K":
        if len(fields) != 4:
            raise CardParseError("K element requires two inductors and a coupling factor")
        return Element(
            name,
            family,
            (),
            "",
            (),
            _expression(fields[3], "coupling"),
            None,
            meta,
            controls=tuple(fields[1:3]),
        )
    if family == "S":
        positional, parameters = _split(fields[1:])
        if len(positional) not in {5, 6}:
            raise CardParseError("S switch requires four nodes and a model")
        tail = positional[5] if len(positional) == 6 else ""
        return Element(
            name, family, tuple(positional[:4]), tail, parameters, None, positional[4], meta
        )
    if family == "W":
        positional, parameters = _split(fields[1:])
        if len(positional) not in {4, 5}:
            raise CardParseError("W switch requires two nodes, a voltage source and a model")
        tail = positional[4] if len(positional) == 5 else ""
        return Element(
            name,
            family,
            tuple(positional[:2]),
            tail,
            parameters,
            None,
            positional[3],
            meta,
            controls=(positional[2],),
        )
    if family == "T":
        positional, parameters = _split(fields[1:])
        if len(positional) != 4:
            raise CardParseError("T line requires four nodes")
        return Element(name, family, tuple(positional), "", parameters, None, None, meta)
    if family == "X":
        positional, parameters = _split_instance(_drop_params_keyword(fields[1:]))
        if len(positional) < 2:
            raise CardParseError(
                "subcircuit instance requires at least one node and a subcircuit name"
            )
        return Element(
            name, family, tuple(positional[:-1]), "", parameters, None, positional[-1], meta
        )
    return Opaque(" ".join(fields), f"unsupported element family {family}", meta)


def _parse_option(fields: list[str], meta: CardMeta) -> Option:
    settings: list[tuple[str, str | None]] = []
    rest = fields[1:]
    index = 0
    while index < len(rest):
        field = rest[index]
        if "=" in field and not field.startswith("="):
            key, value = field.split("=", 1)
            if not value and index + 1 < len(rest):
                index += 1
                value = rest[index]
            settings.append((key, value))
        elif index + 2 < len(rest) and rest[index + 1] == "=":
            settings.append((field, rest[index + 2]))
            index += 2
        else:
            settings.append((field, None))
        index += 1
    return Option(fields[0], tuple(settings), meta)


def _parse_func(code: str, meta: CardMeta) -> Func:
    match = _FUNC_CARD.match(code)
    if not match:
        raise CardParseError(".func must look like .func name(a, b) {expression}")
    raw_parameters = [item.strip() for item in match.group(2).split(",") if item.strip()]
    for parameter in raw_parameters:
        if not _IDENTIFIER.fullmatch(parameter):
            raise CardParseError(f"invalid .func parameter {parameter!r}")
    raw = match.group(3).strip()
    body = _expression(raw, f".func {match.group(1)} body")
    return Func(match.group(1), tuple(raw_parameters), body, raw, meta)


_TAIL_EXPRESSION = re.compile(r"\{([^{}]*)\}|'([^']*)'")


def tail_expressions(tail: str) -> list[tuple[re.Match[str], Expr]]:
    """Find the ``{...}`` and ``'...'`` expressions inside pass-through text."""

    found: list[tuple[re.Match[str], Expr]] = []
    for match in _TAIL_EXPRESSION.finditer(tail):
        found.append((match, _expression(match.group(0), "source expression")))
    return found


@lru_cache(maxsize=4096)
def model_parameters(tail: str) -> tuple[Assignment, ...]:
    """Read the ``name=value`` list of a ``.model`` card (raises CardParseError)."""

    text = tail.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return _assignments(split_fields(_top_level_commas_to_spaces(text)))


def _top_level_commas_to_spaces(text: str) -> str:
    depth = 0
    quote: str | None = None
    characters = list(text)
    for index, character in enumerate(characters):
        if quote:
            if character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character in "({":
            depth += 1
        elif character in ")}":
            depth -= 1
        elif character == "," and depth == 0:
            characters[index] = " "
    return "".join(characters)


def _parse_directive(card: LogicalCard, fields: list[str], meta: CardMeta) -> Statement:
    first = fields[0]
    lowered = first.casefold()
    if lowered in _INCLUDE_KEYWORDS:
        if len(fields) != 2:
            raise CardParseError(".include requires exactly one quoted or unquoted path")
        return Include(_unquote(fields[1]), meta)
    if lowered == ".lib":
        # Two arguments name a file and a section; one argument opens a section.
        if len(fields) == 3:
            return LibCall(_unquote(fields[1]), _unquote(fields[2]), meta)
        if len(fields) == 2:
            section = _unquote(fields[1])
            if not _IDENTIFIER.fullmatch(section):
                raise CardParseError(".lib requires a valid section name")
            return LibSectionStart(section, meta)
        raise CardParseError(".lib requires a section name, or a path and a section name")
    if lowered == ".endl":
        if len(fields) > 2:
            raise CardParseError(".endl accepts at most one section name")
        return LibSectionEnd(_unquote(fields[1]) if len(fields) == 2 else None, meta)
    if lowered == ".param":
        if len(fields) < 2:
            raise CardParseError(".param requires at least one assignment")
        return Param(_assignments(fields[1:]), meta)
    if lowered == ".func":
        return _parse_func(card.code, meta)
    if lowered in _OPTION_KEYWORDS:
        return _parse_option(fields, meta)
    if lowered == ".subckt":
        if len(fields) < 2 or not _IDENTIFIER.fullmatch(fields[1]):
            raise CardParseError(".subckt requires a valid name")
        rest = _drop_params_keyword(fields[2:])
        boundary = _first_assignment_index(rest)
        return SubcktStart(fields[1], tuple(rest[:boundary]), _assignments(rest[boundary:]), meta)
    if lowered == ".ends":
        if len(fields) > 2:
            raise CardParseError(".ends accepts at most one subcircuit name")
        return SubcktEnd(fields[1] if len(fields) == 2 else None, meta)
    if lowered == ".model":
        match = _MODEL_CARD.match(card.code)
        if not match:
            raise CardParseError(".model requires a name and model kind")
        return Model(match.group(1), match.group(2), match.group(3).strip(), meta)
    if lowered == ".global":
        if len(fields) < 2:
            raise CardParseError(".global requires at least one node")
        return Global(tuple(fields[1:]), meta)
    if lowered == ".end":
        if len(fields) != 1:
            raise CardParseError(".end does not accept arguments")
        return End(meta)
    if lowered in DIRECTIVES:
        return Directive(lowered, card.code, meta)
    return Opaque(card.code, f"unsupported directive {first}", meta)


def _parse_card(card: LogicalCard) -> Statement:
    meta = _meta(card)
    if card.kind == "blank":
        return Blank(meta)
    if card.kind == "comment":
        return Comment(card.code, meta)
    if card.kind == "orphan-continuation":
        return Opaque(card.code, "orphan continuation", meta)
    fields = split_fields(card.code)
    if not fields:
        return Blank(meta)
    if fields[0].startswith("."):
        return _parse_directive(card, fields, meta)
    if not _IDENTIFIER.fullmatch(fields[0]):
        raise CardParseError(f"invalid element name {fields[0]!r}")
    return _parse_element(fields, meta)


def _block_meta(cards: list[LogicalCard]) -> CardMeta:
    first, last = cards[0].span, cards[-1].span
    span = SourceSpan(
        first.filename, first.start_line, first.start_col, last.end_line, last.end_col
    )
    segments = tuple(segment for card in cards for segment in card.segments)
    return CardMeta(span, segments, "\n".join(card.original for card in cards))


def _is_keyword(card: LogicalCard, keyword: str) -> bool:
    if card.kind != "code":
        return False
    head = card.code.split(None, 1)[0].casefold() if card.code.strip() else ""
    return head == keyword


def parse_text(text: str, filename: str = "<memory>", *, title_line: bool = False) -> SyntaxDeck:
    """Parse one file.

    With ``title_line`` the file is treated as the top of a deck: ngspice reads
    its first line as a title.  A first line that is not a valid card becomes a
    :class:`Title`; one that is a valid card is kept but reported, because
    ngspice will silently ignore it.
    """

    cards, diagnostics = logical_cards(text, filename)
    statements: list[Statement] = []
    ended = False
    index = 0
    while index < len(cards):
        card = cards[index]
        index += 1
        if ended:
            if card.kind not in {"blank", "comment"}:
                diagnostics.append(
                    Diagnostic(
                        "ST1003",
                        "error",
                        "statement after .end is ignored",
                        card.span,
                    )
                )
            continue
        if _is_keyword(card, ".control"):
            block = [card]
            while index < len(cards) and not _is_keyword(block[-1], ".endc"):
                block.append(cards[index])
                index += 1
            meta = _block_meta(block)
            if not _is_keyword(block[-1], ".endc"):
                diagnostics.append(
                    Diagnostic("ST1006", "error", ".control block has no .endc", card.span)
                )
            statements.append(Control(tuple(item.original for item in block), meta))
            continue
        first_line = title_line and card.span.start_line == 1 and card.kind == "code"
        try:
            statement = _parse_card(card)
        except CardParseError as error:
            if first_line:
                statements.append(Title(card.original, _meta(card)))
                continue
            is_limit = isinstance(error.__cause__, ExpressionLimitError)
            code = "ST1004" if is_limit else "ST1002"
            reason = str(error)
            diagnostics.append(Diagnostic(code, "error", reason, card.span))
            statements.append(Opaque(card.code, reason, _meta(card)))
            continue
        except RecursionError:
            reason = "statement exceeds the parser nesting limit"
            diagnostics.append(Diagnostic("ST1004", "error", reason, card.span))
            statements.append(Opaque(card.code, reason, _meta(card)))
            continue
        if first_line and not isinstance(statement, (Blank, Comment)):
            diagnostics.append(
                Diagnostic(
                    "ST1005",
                    "warning",
                    "ngspice reads the first line of a deck as its title, so this card is "
                    "ignored by ngspice; start the file with a comment",
                    card.span,
                )
            )
        statements.append(statement)
        ended = isinstance(statement, End)
        if isinstance(statement, Opaque):
            diagnostics.append(
                Diagnostic("ST1101", "warning", statement.reason, statement.meta.span)
            )
    return SyntaxDeck(filename, tuple(statements), sorted_diagnostics(diagnostics))
