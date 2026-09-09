import ast
import re
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(".github/workflows/release.yml")


def _distribution_shape_gate() -> ast.Module:
    source = WORKFLOW.read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^          (.+) <<'PY'\n([\s\S]+?)^          PY$", source)
    code = next(code for _command, code in blocks if "distribution_audit" in code)
    return ast.parse(textwrap.dedent(code))


@pytest.mark.parametrize("workflow", sorted(Path(".github/workflows").glob("*.yml")))
def test_project_importing_workflow_heredocs_use_the_installed_environment(
    workflow: Path,
) -> None:
    source = workflow.read_text(encoding="utf-8")
    blocks = re.findall(r"(?m)^          (.+) <<'PY'\n([\s\S]+?)^          PY$", source)
    project_blocks = []
    for command, code in blocks:
        tree = ast.parse(textwrap.dedent(code))
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        imports.extend(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
        if any(name == "spicetrellis" or name.startswith("spicetrellis.") for name in imports):
            project_blocks.append(command)
            assert command.startswith(
                ("uv run --frozen python -I -", ".release-smoke/bin/python -I -")
            ), f"project import uses an interpreter without the installed package: {command}"
    if workflow == WORKFLOW:
        assert len(project_blocks) == 2


def test_release_workflow_binds_and_revalidates_exact_installed_wheel_evidence() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    required = (
        "SYFT_FILE_METADATA_SELECTION: all",
        "path: .release-smoke",
        "output-file: dist/SBOM.spdx.json",
        "release_artifacts.py bind-installed-wheel",
        '--expected-external-path "bin/spice-trellis"',
        "release_artifacts.py validate-sbom",
        "release_artifacts.py write-checksums",
        "release_artifacts.py verify-release",
        "subject-checksums: dist/SHA256SUMS",
    )
    assert all(token in source for token in required)
    assert source.count("src/spicetrellis/release_artifacts.py verify-release") == 3
    assert source.count("ref: ${{ github.sha }}") == 3
    assert "release_evidence" not in source
    assert "spicetrellis.spdx.json" not in source
    assert "subject-path: dist/*" not in source
    assert ".sbom-root" not in source
    assert "python -m spicetrellis.release_artifacts" not in source
    assert 'archive.extractall(destination, filter="data")' in source
    assert 'cd "$source_root/spicetrellis-$RELEASE_PROJECT_VERSION"' in source
    assert source.count("uv sync --frozen --extra dev") == 2
    assert source.count("uv run --frozen pytest") == 2


def test_release_workflow_uploads_and_publishes_only_the_exact_four_assets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    expected_paths = (
        "dist/spicetrellis-${{ env.RELEASE_PROJECT_VERSION }}-py3-none-any.whl",
        "dist/spicetrellis-${{ env.RELEASE_PROJECT_VERSION }}.tar.gz",
        "dist/SBOM.spdx.json",
        "dist/SHA256SUMS",
    )
    assert all(path in source for path in expected_paths)
    assert "gh release create" in source
    assert "path: dist/*" not in source
    assert 'gh release create "$RELEASE_TAG" dist/*' not in source
    assert "dist/*.whl" in source  # Build-time wheel installation, never an upload/publish glob.


def test_release_source_allowlist_contains_only_existing_files() -> None:
    assignment = next(
        node
        for node in _distribution_shape_gate().body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "required_source"
    )
    required_source = ast.literal_eval(assignment.value)
    missing = sorted(path for path in required_source if not Path(path).is_file())
    assert not missing
