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
