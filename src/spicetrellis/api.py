"""Small, stable Python API assembled from the internal pipeline."""

from spicetrellis.elaborate import ElaborationLimits, flatten
from spicetrellis.emit import format_deck
from spicetrellis.fuzzing import FuzzStats, fuzz_smoke
from spicetrellis.interop import structural_summary
from spicetrellis.parser import parse_text
from spicetrellis.semantics import AnalysisLimits, analyze_file, inventory

__all__ = [
    "AnalysisLimits",
    "ElaborationLimits",
    "FuzzStats",
    "analyze_file",
    "flatten",
    "format_deck",
    "fuzz_smoke",
    "inventory",
    "parse_text",
    "structural_summary",
]
