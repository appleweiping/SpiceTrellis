from __future__ import annotations

import re
import tomllib
from pathlib import Path


def test_transitional_checkout_dco_is_marked_until_trusted_context_is_required() -> None:
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Transitional bootstrap" in ci
    assert "\n  dco:" in ci
    assert "git rev-list" in ci


def test_actions_are_pinned_to_full_commits() -> None:
    workflows = tuple(Path(".github/workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("uses:"):
                reference = line.partition("uses:")[2].strip()
                assert re.fullmatch(r"[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?", reference), reference


def _assert_tag_trust_chain(workflow_name: str, tag_pattern: str) -> str:
    body = Path(f".github/workflows/{workflow_name}").read_text(encoding="utf-8")
    assert tag_pattern in body
    assert "git show origin/main:.github/allowed_signers" in body
    assert 'git verify-tag "$GITHUB_REF_NAME"' in body
    assert 'git rev-parse "refs/tags/$GITHUB_REF_NAME^{}"' in body
    assert 'if [ "$resolved" != "$GITHUB_SHA" ]' in body
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in body
    assert ".commit.verification.verified" in body
    assert "head_sha=$GITHUB_SHA&per_page=100" in body
    assert '.event == \\"push\\"' in body
    assert '.head_branch == \\"main\\"' in body
    assert '.head_sha == \\"$GITHUB_SHA\\"' in body
    assert "ref: ${{ github.sha }}" in body
    return body


def test_python_and_go_releases_require_the_exact_signed_verified_main_commit() -> None:
    release = _assert_tag_trust_chain("release.yml", "refs/tags/v*) ;;")
    go_release = _assert_tag_trust_chain("go-module-release.yml", "refs/tags/interop/go/v*) ;;")
    assert "if: needs.verify-ci.result == 'success'" in release
    assert "if: needs.verify-ci.result == 'success'" in go_release
    assert "github.event_name == 'workflow_dispatch' ||" not in release
    assert "github.event_name == 'workflow_dispatch' ||" not in go_release


def test_release_binds_and_revalidates_exact_installed_wheel_evidence() -> None:
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    for token in (
        "SYFT_FILE_METADATA_SELECTION: all",
        "path: .release-smoke",
        "output-file: dist/SBOM.spdx.json",
        "release_artifacts.py bind-installed-wheel",
        '--expected-external-path "bin/spice-trellis"',
        "release_artifacts.py validate-sbom",
        "release_artifacts.py write-checksums",
        "subject-checksums: dist/SHA256SUMS",
    ):
        assert token in release
    assert release.count("src/spicetrellis/release_artifacts.py verify-release") == 3
    assert "subject-path: dist/*" not in release
    assert "path: dist/*" not in release
    assert 'gh release create "$RELEASE_TAG" dist/*' not in release


def test_source_archive_includes_the_files_needed_by_its_frozen_tests() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    profile = config["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert {"/.github/workflows", "/.github/allowed_signers", "/uv.lock"} <= set(profile["include"])
    assert not {"/.github", "/.github/workflows", "/uv.lock"} & set(profile.get("exclude", []))
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    for name in (
        ".github/workflows/ci.yml",
        ".github/workflows/dco.yml",
        ".github/workflows/go-module-release.yml",
        ".github/workflows/release.yml",
        ".github/allowed_signers",
        "docs/schemas/simulation-result-v1.schema.json",
        "tools/check_ngspice_results.py",
        "uv.lock",
    ):
        assert f'"{name}",' in release
    assert "Run the full suite from the exact source distribution" in release
    assert 'mktemp -d "$RUNNER_TEMP/spicetrellis-sdist.XXXXXX"' in release
