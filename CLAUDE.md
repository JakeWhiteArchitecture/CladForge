# CladForge — working notes

## Pull requests

**Always check the PR number and its state before pushing to its branch or merging.**
Read the PR (state, `merged_at`, and whether its head is an ancestor of `main`) rather
than assuming the branch you last used is still the live one. This has bitten the repo
three times:

- PR #8 was merged into `claude/cladforge-build-wizard` a minute *after* that branch had
  already merged to `main` through PR #7, so its work never reached `main`.
- A commit pushed to PR #10's branch landed after #10 had already merged, stranding it.

Before any push, run:

```bash
git fetch origin main <branch>
git merge-base --is-ancestor <branch> origin/main && echo "already merged"
git log --oneline origin/main..HEAD          # what main is actually missing
```

**Start each new piece of work from a fresh branch off the latest `main`.** Branches move
fast here. Avoid stacked PRs; if one is unavoidable, say in the body which PR must merge
first, and re-check after it does.

## Testing

Both suites run before any push:

```bash
python -m pytest -q                                    # engine + export
VENDOR_DIR=... PLAYWRIGHT_MODULE=... node tests/smoke.js  # browser, needs app.py running
```

Restart `app.py` after editing `templates/index.html` — Jinja caches the template and a
stale one produces confusing browser failures.

## Runtime pins are a matched set

Pyodide, the IfcOpenShell WASM wheel and Shapely must agree. The wheel's ABI tag has to
match the Pyodide build, and Shapely must exist for that build or the whole engine stops.
Currently Pyodide 0.29.0 (CPython 3.13, `pyodide_2025_0`) with IfcOpenShell 0.8.5, which
carries IFC4X3_ADD2. Never bump one alone.

## The engine dies rather than raises

A C++ abort inside WASM — GEOS, IfcOpenShell, or running out of heap — kills the Pyodide
runtime outright. `try`/`except` cannot catch it. So: ask before you try (see
`_create_file` probing `schema_names()`), bound anything unbounded (see
`CONTEXT_TRI_BUDGET`), and keep `restartEngine()` working as the way back.
