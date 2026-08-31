"""Bounded deterministic mutation smoke tests for parser robustness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256

from spicetrellis.parser import parse_text


@dataclass(frozen=True, slots=True)
class FuzzStats:
    cases: int
    bytes_examined: int
    diagnostics: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _mutate(text: str, index: int, seed: int) -> str:
    digest = sha256(f"{seed}:{index}".encode()).digest()
    if not text:
        return "* empty seed\n"
    position = int.from_bytes(digest[:4], "big") % (len(text) + 1)
    additions = (" ", "\n* mutation\n", "{}", "+ ", "\t")
    addition = additions[digest[4] % len(additions)]
    if digest[5] % 3 == 0 and position < len(text):
        return text[:position] + text[position + 1 :]
    return text[:position] + addition + text[position:]


def fuzz_smoke(text: str, *, cases: int = 128, seed: int = 0) -> FuzzStats:
    """Parse bounded deterministic mutations and return reproducible work counts."""

    if isinstance(cases, bool) or not isinstance(cases, int) or not 1 <= cases <= 10_000:
        raise ValueError("cases must be an integer from 1 through 10000")
    diagnostics = 0
    examined = 0
    for index in range(cases):
        mutated = _mutate(text, index, seed)
        examined += len(mutated.encode("utf-8"))
        diagnostics += len(parse_text(mutated, f"<mutation-{index}>").diagnostics)
    return FuzzStats(cases, examined, diagnostics)
