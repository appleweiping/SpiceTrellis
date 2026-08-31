"""SpiceTrellis public API."""

from spicetrellis._version import __version__
from spicetrellis.api import (
    AnalysisLimits,
    ElaborationLimits,
    analyze_file,
    flatten,
    format_deck,
    fuzz_smoke,
    inventory,
    parse_text,
    structural_summary,
)

__all__ = [
    "AnalysisLimits",
    "ElaborationLimits",
    "__version__",
    "analyze_file",
    "flatten",
    "format_deck",
    "fuzz_smoke",
    "inventory",
    "parse_text",
    "structural_summary",
]
