from __future__ import annotations

from dataclasses import replace

import pytest

from spicetrellis.simulation_results import (
    ResultError,
    ResultLimits,
    ResultPlot,
    ResultVariable,
    SimulationResult,
)


def plot() -> ResultPlot:
    return ResultPlot(
        "original", "op", "real", (ResultVariable("v(out)", "voltage"),), ((1.0,),), (1,)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"analysis": True},
        {"encoding": 1},
        {"encoding": "other"},
        {"variables": []},
        {"variables": ()},
        {"variables": ("not-a-variable",)},
        {"columns": [[1.0]]},
        {"columns": ([1.0],)},
        {"columns": ()},
        {"columns": ((1,),)},
        {"columns": ((True,),)},
        {"columns": ((complex(1, 0),),)},
        {"columns": ((),)},
        {"columns": ((float("inf"),),)},
        {"dimensions": (True,)},
        {"dimensions": ()},
        {"dimensions": (2,)},
        {"dimensions": [1]},
        {"metadata": []},
        {"metadata": (("a", "b", "c"),)},
        {"metadata": ("ab",)},
        {"metadata": (("name", "value"),) * 129},
        {"metadata": (("name", "\u200d"),)},
    ],
)
def test_public_plot_constructor_rejects_invalid_immutable_values(changes) -> None:
    with pytest.raises(ResultError):
        replace(plot(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "bad name"},
        {"name": "e\u0301"},
        {"quantity": None},
        {"display_grid": -1},
        {"display_grid": True},
        {"display_grid": 256},
    ],
)
def test_public_variable_constructor_rejects_ambiguous_names_and_grid(changes) -> None:
    with pytest.raises(ResultError):
        replace(ResultVariable("v(out)", "voltage"), **changes)


@pytest.mark.parametrize(
    "changes",
    [{"format": ""}, {"source_sha256": "A" * 64}, {"plots": []}, {"plots": ()}, {"plots": (1,)}],
)
def test_result_constructor_rejects_invalid_identity_and_plot_collection(changes) -> None:
    with pytest.raises(ResultError):
        replace(SimulationResult("spice-raw", "0" * 64, (plot(),)), **changes)


def test_result_hard_variable_ceiling_applies_across_plots() -> None:
    wide = ResultPlot(
        "original",
        "op",
        "real",
        tuple(ResultVariable(f"v(n{i})", "voltage") for i in range(8_193)),
        ((1.0,),) * 8_193,
        (1,),
    )
    with pytest.raises(ResultError, match="variable limit"):
        SimulationResult("spice-raw", "0" * 64, (wide, wide))


def test_all_limits_reject_noninteger_and_over_ceiling_values() -> None:
    for name in (
        "max_bytes",
        "max_plots",
        "max_variables",
        "max_points",
        "max_cells",
        "max_line_bytes",
    ):
        for value in (0, -1, True, 1.5, "1", 1 << 40):
            with pytest.raises(ResultError, match="limit"):
                ResultLimits(**{name: value})
