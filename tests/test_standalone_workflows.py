from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "workflow,script,arguments,expected",
    [
        ("dco.yml", "dco.py", [], 2),
        ("release.yml", "release_artifacts.py", ["--help"], 0),
    ],
)
def test_actual_standalone_workflow_command_is_import_isolated(
    workflow: str,
    script: str,
    arguments: list[str],
    expected: int,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    body = (root / ".github/workflows" / workflow).read_text(encoding="utf-8")
    relative = f"src/spicetrellis/{script}"
    invocations = tuple(
        line.strip() for line in body.splitlines() if "python" in line and relative in line
    )
    isolated = bool(invocations) and all(
        f"python -I -S {relative}" in invocation for invocation in invocations
    )
    command = [sys.executable, "-I", "-S", str(root / relative), *arguments]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, result.stderr
    assert "usage:" in result.stdout + result.stderr
    assert isolated, "standalone trusted helpers must disable sibling and site-package imports"
