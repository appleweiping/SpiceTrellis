"""Small, stable Python API assembled from the internal pipeline."""

from spicetrellis.elaborate import ElaborationLimits, flatten
from spicetrellis.emit import format_deck
from spicetrellis.fuzzing import FuzzStats, fuzz_smoke
from spicetrellis.interop import structural_summary
from spicetrellis.ir import (
    IR_SCHEMA,
    IR_VERSION,
    CircuitIR,
    CircuitIRError,
    IRInstance,
    IRLoss,
    IRModel,
    IRModule,
    IRParameter,
    IRSource,
    build_ir,
    dump_ir,
    load_ir,
    load_ir_text,
    validate_ir,
    write_ir,
)
from spicetrellis.parser import parse_text
from spicetrellis.semantics import AnalysisLimits, analyze_file, inventory

__all__ = [
    "IR_SCHEMA",
    "IR_VERSION",
    "AnalysisLimits",
    "CircuitIR",
    "CircuitIRError",
    "ElaborationLimits",
    "FuzzStats",
    "IRInstance",
    "IRLoss",
    "IRModel",
    "IRModule",
    "IRParameter",
    "IRSource",
    "analyze_file",
    "build_ir",
    "dump_ir",
    "flatten",
    "format_deck",
    "fuzz_smoke",
    "inventory",
    "load_ir",
    "load_ir_text",
    "parse_text",
    "structural_summary",
    "validate_ir",
    "write_ir",
]
