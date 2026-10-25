"""Acceptance tests against the real SKY130 model library.

The PDK is not vendored.  Fetch it first, then point the tests at it:

    python tools/fetch_sky130.py --dest .cache
    python tools/fetch_sky130.py --source upstream --dest .cache/sky130_fd_pr
    SPICETRELLIS_PDK_CACHE=.cache pytest tests/test_sky130.py

With ``ngspice`` on PATH (or ``SPICETRELLIS_NGSPICE`` set), the flat deck is
also simulated and its operating point compared with the hierarchical deck.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from spicetrellis import analyze_file, flatten, format_deck
from spicetrellis.model import Element, Model

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "sky130_opamp" / "two_stage_ota_tb.spice"
CACHE = os.environ.get("SPICETRELLIS_PDK_CACHE")
CIEL_LIBRARY = Path(CACHE or ".") / "sky130A" / "libs.tech" / "ngspice" / "sky130.lib.spice"
UPSTREAM_LIBRARY = Path(CACHE or ".") / "sky130_fd_pr" / "models" / "sky130.lib.spice"

needs_ciel = pytest.mark.skipif(
    not CACHE or not CIEL_LIBRARY.exists(), reason="set SPICETRELLIS_PDK_CACHE to a fetched sky130A"
)
needs_upstream = pytest.mark.skipif(
    not CACHE or not UPSTREAM_LIBRARY.exists(),
    reason="set SPICETRELLIS_PDK_CACHE to a fetched upstream sky130_fd_pr",
)

# What lint reports in google/skywater-pdk-libs-sky130_fd_pr v0.20.1, corner tt.
# open_pdks rewrites each of these before ngspice sees the files; ngspice 46
# stops at the first one ("Not enough parameters for i source").
UPSTREAM_DEFECTS = {
    # `include` without the leading dot reads as a current source.
    ("sky130_fd_pr__nfet_05v0_nvt.pm3.spice", "ST1002", "I source"),
    # A resistor card that lost its leading `r` reads as a switch.
    ("r+c.mrp1monte.spice", "ST1002", "S switch"),
    # Spectre's `dev/gauss=` Monte Carlo annotation is not SPICE.
    ("sky130_fd_pr__cap_mim_m3_1.model.spice", "ST1002", "czero"),
    ("sky130_fd_pr__cap_mim_m3_2.model.spice", "ST1002", "czero"),
    ("sky130_fd_pr__cap_var_lvt.model.spice", "ST1002", "cm1"),
    # `temp` where ngspice only knows `temper`.
    ("sky130_fd_pr__res_high_po_0p35.model.spice", "ST2102", "temp"),
}


def _deck(tmp_path: Path, library: Path) -> Path:
    text = EXAMPLE.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^\.lib .*$", f".lib {library.resolve().as_posix()} tt", text)
    deck = tmp_path / EXAMPLE.name
    deck.write_text(text, encoding="utf-8")
    return deck


def _roots(tmp_path: Path, library: Path) -> tuple[Path, ...]:
    return (tmp_path, library.resolve().parents[2])


@needs_ciel
def test_the_opamp_lints_clean_against_open_pdks_sky130a(tmp_path: Path) -> None:
    analysis = analyze_file(
        _deck(tmp_path, CIEL_LIBRARY), include_roots=_roots(tmp_path, CIEL_LIBRARY)
    )
    assert analysis.diagnostics == ()
    assert len(analysis.dependencies) > 100


@needs_ciel
def test_the_flat_opamp_is_a_standalone_deck(tmp_path: Path) -> None:
    analysis = analyze_file(
        _deck(tmp_path, CIEL_LIBRARY), include_roots=_roots(tmp_path, CIEL_LIBRARY)
    )
    result = flatten(analysis)
    assert result.diagnostics == ()
    devices = [item for item in result.statements if isinstance(item, Element)]
    mosfets = [item for item in devices if item.family == "M"]
    assert len(mosfets) == 8
    assert {item.name.split(".")[2] for item in mosfets} == {f"XM{index}" for index in range(1, 9)}
    models = {item.name.rpartition(".")[0] for item in result.statements if isinstance(item, Model)}
    assert {item.model for item in mosfets} <= models
    flat = tmp_path / "flat.spice"
    flat.write_text(format_deck(result.statements), encoding="utf-8")
    again = analyze_file(flat)
    assert not again.has_errors
    assert len(again.dependencies) == 1


def _ngspice() -> str | None:
    return os.environ.get("SPICETRELLIS_NGSPICE") or shutil.which("ngspice")


def _operating_point(ngspice: str, deck: Path) -> dict[str, float]:
    completed = subprocess.run(
        [ngspice, "-b", deck.name],
        cwd=deck.parent,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    values = dict(re.findall(r"^(\S+) = (\S+)$", completed.stdout, re.MULTILINE))
    assert values, completed.stdout + completed.stderr
    return {key: float(value) for key, value in values.items()}


@needs_ciel
@pytest.mark.skipif(_ngspice() is None, reason="ngspice is not installed")
def test_ngspice_gives_the_same_operating_point_for_the_flat_deck(tmp_path: Path) -> None:
    ngspice = _ngspice()
    assert ngspice is not None
    deck = _deck(tmp_path, CIEL_LIBRARY)
    result = flatten(analyze_file(deck, include_roots=_roots(tmp_path, CIEL_LIBRARY)))
    flat = tmp_path / "flat.spice"
    flat.write_text(format_deck(result.statements), encoding="utf-8")
    hierarchical = _operating_point(ngspice, deck)
    flattened = _operating_point(ngspice, flat)
    assert set(hierarchical) == {"v(out)", "v(x1.net2)", "i(v1)"}
    assert set(flattened) == set(hierarchical)
    for name, value in hierarchical.items():
        assert flattened[name] == pytest.approx(value, rel=1e-5), name
    # The output follows the 0.9 V input: the amplifier is biased correctly.
    assert hierarchical["v(out)"] == pytest.approx(0.9, abs=0.01)


@needs_upstream
def test_lint_finds_what_open_pdks_has_to_patch_in_upstream_sky130_fd_pr(
    tmp_path: Path,
) -> None:
    analysis = analyze_file(
        _deck(tmp_path, UPSTREAM_LIBRARY), include_roots=_roots(tmp_path, UPSTREAM_LIBRARY)
    )
    errors = [item for item in analysis.diagnostics if item.severity == "error"]
    for filename, code, fragment in UPSTREAM_DEFECTS:
        assert any(
            Path(item.primary.filename).name == filename
            and item.code == code
            and fragment in item.message
            for item in errors
        ), (filename, code, fragment)
    files = {Path(item.primary.filename).name for item in errors}
    expected_files = {filename for filename, _code, _fragment in UPSTREAM_DEFECTS}
    # `temp` also appears in other resistor and RF-transistor models.
    unexplained = {
        item
        for item in errors
        if Path(item.primary.filename).name not in expected_files
        and not (item.code in {"ST2102", "ST2105"} and "'temp'" in item.message)
        and not (item.code in {"ST2102", "ST2105"} and Path(item.primary.filename).name in files)
    }
    assert not unexplained, unexplained
