# Contributing

Thank you for helping improve SpiceTrellis.

## Before opening a change

Open an issue for a new syntax family or behavioral change. State the exact
syntax boundary, expected diagnostics, and whether existing decks change.
Security reports must follow `SECURITY.md`, not a public issue.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Run all local gates before submitting:

```bash
ruff check .
ruff format --check .
pytest --cov=spicetrellis --cov-report=term-missing
python -m build
```

## Change requirements

- Keep runtime dependencies at zero unless an issue establishes a compelling
  maintenance and security case.
- Never execute circuit text, parameter expressions, includes, or comments.
- Preserve unsupported cards instead of silently dropping them.
- Add tests for valid, invalid, and boundary inputs.
- Keep output deterministic across operating systems and hash seeds.
- Update README, architecture notes, and changelog when public behavior changes.
- Use original fixtures that contain no proprietary PDK or model data.

Pull requests should be focused, explain their compatibility effect, and list
the exact commands that were actually run. By participating, contributors agree
to follow the Code of Conduct.

## Commit and release evidence

Every pull-request commit needs its author's matching `Signed-off-by` trailer;
use `git commit -s`. The required `DCO / commits` status runs only the exact
trusted base verifier under `python -I -S`, without installing dependencies or
executing pull-request code. It binds base repository/ref/SHA, head and commit
count before and after metadata download and before publishing the result.
Retarget edits rerun the verifier and reset the event head to pending.

Python and nested Go releases require an annotated SSH-signed version tag,
the allowed-signer policy from protected `main`, a GitHub-verified source commit,
main ancestry and successful complete main-push CI on that exact commit. Manual
dispatch is not an exception. Python releases re-test the audited frozen source
archive, install the wheel separately, bind its complete RECORD and launcher to
the pinned Syft SPDX profile, and publish exactly four checksum-verified assets
with build provenance. These infrastructure helpers are not counted as additional
circuit or simulator functionality.
