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
from spicetrellis.physical import PhysicalError, PhysicalLibrary, flatten_layout
from spicetrellis.physical_json import (
    PHYSICAL_SCHEMA,
    PHYSICAL_VERSION,
    dump_physical,
    load_physical,
    load_physical_text,
    physical_data,
    physical_digest,
    write_physical,
)
from spicetrellis.raw_results import load_raw, parse_raw
from spicetrellis.result_json import (
    RESULT_SCHEMA,
    RESULT_VERSION,
    dump_result,
    load_result,
    load_result_text,
    write_result,
)
from spicetrellis.semantics import AnalysisLimits, analyze_file, inventory
from spicetrellis.simulation_results import (
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
)
from spicetrellis.xyce_results import load_xyce_csv, parse_xyce_csv

__all__ = [
    "IR_SCHEMA",
    "IR_VERSION",
    "PHYSICAL_SCHEMA",
    "PHYSICAL_VERSION",
    "RESULT_SCHEMA",
    "RESULT_VERSION",
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
    "PhysicalError",
    "PhysicalLibrary",
    "ResultError",
    "ResultLimits",
    "ResultPlot",
    "ResultVariable",
    "SimulationResult",
    "analyze_file",
    "build_ir",
    "dump_ir",
    "dump_physical",
    "dump_result",
    "flatten",
    "flatten_layout",
    "format_deck",
    "fuzz_smoke",
    "inventory",
    "load_ir",
    "load_ir_text",
    "load_physical",
    "load_physical_text",
    "load_raw",
    "load_result",
    "load_result_text",
    "load_xyce_csv",
    "parse_raw",
    "parse_text",
    "parse_xyce_csv",
    "physical_data",
    "physical_digest",
    "structural_summary",
    "validate_ir",
    "write_ir",
    "write_physical",
    "write_result",
]
