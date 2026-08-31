"""Small, stable Python API assembled from the internal pipeline."""

from spicetrellis.elaborate import flatten
from spicetrellis.emit import format_deck
from spicetrellis.parser import parse_text
from spicetrellis.semantics import analyze_file, inventory

__all__ = ["analyze_file", "flatten", "format_deck", "inventory", "parse_text"]
