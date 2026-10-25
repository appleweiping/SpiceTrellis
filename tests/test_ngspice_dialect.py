"""The ngspice syntax that real PDK libraries and xschem netlists use."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from spicetrellis import analyze_file, flatten, format_deck, parse_text
from spicetrellis.expressions import (
    ExpressionError,
    evaluate_expression,
    fold_expression,
    parse_expression,
    spice_expression,
)
from spicetrellis.model import Comment, Control, Directive, Element, Func, Model, Option, Title


def _deck(tmp_path: Path, text: str, name: str = "deck.sp") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _codes(analysis) -> set[str]:
    return {item.code for item in analysis.diagnostics}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("10pF", Decimal("1e-11")),
        ("1.8V", Decimal("1.8")),
        ("2meg", Decimal("2e6")),
        ("3a", Decimal("3e-18")),
        ("sqrt(16) + abs(-1)", Decimal("5")),
        ("pow(2, 10)", Decimal("1024")),
        ("pwr(-2, 2)", Decimal("-4")),
        ("min(3, 1, 2) + max(1, 5)", Decimal("6")),
        ("int(2.7) + nint(2.5) + floor(-1.5)", Decimal("3")),
        ("(1 < 2) && !(3 == 4)", Decimal("1")),
        ("0 ? 10 : 20", Decimal("20")),
        ("'2*3'", Decimal("6")),
        ("{1} + {2}", Decimal("3")),
    ],
)
def test_ngspice_expression_forms(source: str, expected: Decimal) -> None:
    assert evaluate_expression(parse_expression(source), {}) == expected


def test_simulation_time_terms_stay_symbolic_and_probes_are_renamed() -> None:
    expression = parse_expression("{r0 * (1 + v(a, b) * tc) + agauss(0, 1, 3) + temper}")
    folded = fold_expression(
        expression,
        {"r0": Decimal(100), "tc": Decimal("0.5")},
        node=lambda name: f"X1.{name}",
    )
    assert spice_expression(folded) == "{100*(1+v(X1.a,X1.b)*0.5)+agauss(0,1,3)+temper}"
    with pytest.raises(ExpressionError, match="simulation-time"):
        evaluate_expression(expression, {"r0": Decimal(1), "tc": Decimal(1)})
    with pytest.raises(ExpressionError, match="undefined parameter 'temp'"):
        evaluate_expression(parse_expression("temp - 27"), {})


def test_every_device_family_parses_and_renders() -> None:
    source = """* families
D1 a 0 dmod area='w*l'
Q1 c b e s qnpn
J1 d g s jmod
M1 d g s b nch w=1u l=0.15u
E1 out 0 inp inn 1e5
E2 out2 0 value={v(a)*2}
G1 a b poly(2) c d e f 0 1 1
F1 a b vsense 2
H1 a b vsense 1k
B1 out 0 V = v(a) * 2 + 1
K1 L1 L2 0.99
L1 a b 1u
L2 c d 1u
R1 a b r = {(154*x)}
C1 a b 10pF
Cq a b q='1p*v(a,b)'
S1 a b c d swmod off
W1 a b vsense wmod
T1 a 0 b 0 z0=50 td=1n
X1 a b sub params: w=1
"""
    deck = parse_text(source)
    assert [item for item in deck.diagnostics if item.severity == "error"] == []
    elements = {item.name: item for item in deck.statements if isinstance(item, Element)}
    assert elements["Q1"].nodes == ("c", "b", "e", "s")
    assert elements["Q1"].model == "qnpn"
    assert elements["G1"].form == "poly(2)"
    assert elements["G1"].nodes == ("a", "b", "c", "d", "e", "f")
    assert elements["H1"].controls == ("vsense",)
    assert elements["K1"].controls == ("L1", "L2")
    assert elements["E2"].form == "behavioral"
    rendered = format_deck(deck)
    assert "B1 out 0 V={v(a)*2+1}" in rendered
    assert "D1 a 0 dmod area={w*l}" in rendered
    assert "G1 a b poly(2) c d e f 0 1 1" in rendered
    assert "W1 a b vsense wmod" in rendered
    assert "X1 a b sub w=1" in rendered


def test_comment_lines_inside_a_continued_card_do_not_end_it() -> None:
    deck = parse_text(
        ".model nch nmos\n* DC parameters\n+ level=54 $ BSIM4\n\n+ vth0={0.4+dv}\n.end\n"
    )
    model = next(item for item in deck.statements if isinstance(item, Model))
    assert model.tail == "level=54 vth0={0.4+dv}"
    assert any(isinstance(item, Comment) for item in deck.statements)
    assert not deck.diagnostics


def test_simulator_cards_are_kept_and_never_interpreted() -> None:
    deck = parse_text(
        ".option scale=1.0u noacct\n.func f(a, b) {a*b + 1}\n.tran 1n 1u\n"
        ".control\nshell rm -rf /\nrun\n.endc\n.end\n"
    )
    kinds = [type(item) for item in deck.statements]
    assert kinds[:4] == [Option, Func, Directive, Control]
    option = deck.statements[0]
    assert isinstance(option, Option)
    assert option.settings == (("scale", "1.0u"), ("noacct", None))
    assert not deck.diagnostics


def test_the_first_line_is_a_title_like_ngspice_reads_it(tmp_path: Path) -> None:
    titled = analyze_file(_deck(tmp_path, "Two stage op amp\nR1 a 0 1k\n.end\n"))
    assert isinstance(titled.deck.top[0], Title)
    assert not titled.diagnostics
    lost = analyze_file(_deck(tmp_path, "R1 a 0 1k\nR2 a 0 2k\n.end\n", "lost.sp"))
    assert [item.code for item in lost.diagnostics] == ["ST1005"]


def test_model_references_are_checked(tmp_path: Path) -> None:
    analysis = analyze_file(
        _deck(
            tmp_path,
            "* models\n.param rval=2k\nM1 d g 0 0 nofet\nR1 a 0 rval\nR2 a 0 rpoly\n"
            ".model rpoly r rsh=100\n.end\n",
        )
    )
    by_code = {item.code: item for item in analysis.diagnostics}
    assert "nofet" in by_code["ST2215"].message
    assert "rval" in by_code["ST2216"].message
    assert sum(item.code == "ST2215" for item in analysis.diagnostics) == 1


def test_a_redefined_parameter_warns_and_the_last_definition_wins(tmp_path: Path) -> None:
    analysis = analyze_file(_deck(tmp_path, "* t\n.param r=1k\nR1 a 0 {r}\n.param r=4k\n.end\n"))
    assert [item.code for item in analysis.diagnostics] == ["ST2101"]
    assert "R1 a 0 4k" in format_deck(flatten(analysis).statements)


XSCHEM = """** sch_path: tb.sch
x1 VDD GND out cell
V1 VDD GND 1.8
.subckt cell VDD VSS out
XM1 out out VSS VSS fet L=0.5 W=2 nf=2 ad='int((nf+1)/2) * W/nf * 0.29' mult=1 m=3
R1 VDD out 10k m=1
.ends
.subckt fet d g s b
.param l=1 w=1 ad=0 nf=1 mult=1
M0 d g s b fet_model l={l} w={w} ad={ad} nf={nf}
.model fet_model.0 nmos lmin=0 lmax=10 vth0={0.4+0.01/sqrt(l*w*mult)}
.model fet_model.1 nmos lmin=10 lmax=20 vth0={0.4+0.01/sqrt(l*w*mult)}
.ends
.GLOBAL GND
.end
"""


def test_xschem_style_instance_flattens_like_ngspice_expands_it(tmp_path: Path) -> None:
    analysis = analyze_file(_deck(tmp_path, XSCHEM))
    assert not analysis.has_errors, analysis.diagnostics
    result = flatten(analysis)
    assert not result.has_errors, result.diagnostics
    rendered = format_deck(result.statements)
    # ngspice naming: the device letter first, then the instance path.
    assert "M.x1.XM1.M0 out out GND GND fet_model_1 l=0.5 w=2 ad=0.29 nf=2 m=3" in rendered
    assert "R.x1.R1 VDD out 10k m=1" in rendered
    assert rendered.startswith("** sch_path: tb.sch\n")
    # Local binned models are copied out with their bin suffix and folded values.
    assert ".model fet_model_1.0 nmos lmin=0 lmax=10 vth0=0.41" in rendered
    assert ".model fet_model_1.1 nmos" in rendered


def test_identical_local_models_are_copied_out_once(tmp_path: Path) -> None:
    text = XSCHEM.replace(
        "R1 VDD out 10k m=1",
        "XM2 out out VSS VSS fet L=0.5 W=2 nf=2 mult=1\nXM3 out out VSS VSS fet L=1 W=2 mult=1",
    )
    result = flatten(analyze_file(_deck(tmp_path, text)))
    assert not result.has_errors, result.diagnostics
    models = [item.name for item in result.statements if isinstance(item, Model)]
    assert models == ["fet_model_1.0", "fet_model_1.1", "fet_model_2.0", "fet_model_2.1"]


def test_instance_values_beat_body_params_and_m_multiplies(tmp_path: Path) -> None:
    deck = _deck(
        tmp_path,
        "* t\n.subckt sub a b\n.param l=1 w=1\nR1 a b r={l*w*1000}\nC1 a b 1p m=2\n.ends\n"
        "V1 a 0 1\nX1 a 0 sub l=2 w=3 m=2\n.end\n",
    )
    rendered = format_deck(flatten(analyze_file(deck)).statements)
    assert "R.X1.R1 a 0 r=6000 m=2" in rendered
    assert "C.X1.C1 a 0 1p m=4" in rendered


def test_references_to_other_devices_are_renamed(tmp_path: Path) -> None:
    deck = _deck(
        tmp_path,
        "* t\n.func double(x) {2*x}\n.subckt sense a b\nVs a mid 0\nRs mid b 1\n"
        "F1 b 0 Vs 2\nB1 b 0 i={double(i(Vs)) + v(mid)}\n"
        "L1 a 0 1u\nL2 b 0 1u\nK1 L1 L2 0.9\n.ends\n"
        "X1 in out sense\n.end\n",
    )
    analysis = analyze_file(deck)
    assert not analysis.has_errors, analysis.diagnostics
    rendered = format_deck(flatten(analysis).statements)
    assert "F.X1.F1 out 0 V.X1.Vs 2" in rendered
    assert "B.X1.B1 out 0 i={2*i(V.X1.Vs)+v(X1.mid)}" in rendered
    assert "K.X1.K1 L.X1.L1 L.X1.L2 0.9" in rendered


def test_a_dangling_control_reference_is_an_error(tmp_path: Path) -> None:
    analysis = analyze_file(
        _deck(tmp_path, "* t\nF1 a 0 Vmissing 2\nK1 La Lb 0.5\nLa a 0 1u\n.end\n")
    )
    messages = [item.message for item in analysis.diagnostics if item.code == "ST2217"]
    assert len(messages) == 2
