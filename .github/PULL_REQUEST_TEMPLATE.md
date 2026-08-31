## Summary

## Syntax/API and compatibility impact

## Verification

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `pytest --cov=spicetrellis --cov-report=term-missing`
- [ ] `python -m build`

## Safety and provenance

- [ ] Unsupported input remains visible and no user text is executed.
- [ ] New fixtures are original and contain no proprietary data.
- [ ] Diagnostics and output remain deterministic.
- [ ] Documentation and changelog are updated when behavior changes.
