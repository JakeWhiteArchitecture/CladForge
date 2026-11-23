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

Two suites:

```bash
python -m pytest -q                                    # engine + export
VENDOR_DIR=... PLAYWRIGHT_MODULE=... node tests/smoke.js  # browser, needs app.py running
```

GitHub runs both on every pull request and on `main` (`.github/workflows/tests.yml`, the
*Tests* checks on the PR). So: run pytest before any push; the browser suite need not be
run by hand first, but **a PR is not ready to merge until both checks are green**, and a
red one is fixed before anything else. Run the browser suite by hand when working on
browser behaviour or chasing a CI failure.

`README.md` merges by union (`.gitattributes`): two branches adding or changing neighbouring
rows of the feature table merge without conflict. If both rewrote the same row, both
versions are kept: after a merge, look for a doubled row and delete the stale one.

Several branches can be tested side by side: give each `app.py` its own `PORT` and its
smoke test the matching `SMOKE_URL` (`PORT=8081 python3 app.py`, `SMOKE_URL=http://127.0.0.1:8081/`).

Restart `app.py` after editing `templates/index.html` — Jinja caches the template and a
stale one produces confusing browser failures.

## Runtime pins are a matched set

Pyodide, the IfcOpenShell WASM wheel and Shapely must agree. The wheel's ABI tag has to
match the Pyodide build, and Shapely must exist for that build or the whole engine stops.
Currently Pyodide 0.29.5 (CPython 3.13, ABI `2025_0`) with IfcOpenShell 0.8.5 (its
`pyodide_2025_0` wheel, which 0.29.5 still accepts), which carries IFC4X3_ADD2. Never bump
one alone.

## The engine dies rather than raises

A C++ abort inside WASM — IfcOpenShell, or running out of heap — kills the Pyodide
runtime outright. `try`/`except` cannot catch it. GEOS used to as well: Pyodide 0.29.0's
Shapely aborted on any GEOS error (a TopologyException from one bad shape in the model took
the engine down). 0.29.5's Shapely raises `GEOSException` instead, and the smoke test checks
it does; keep the robust-overlay habits (`_safe`, the 0.01 mm grid) all the same, since a
caught error still loses that element. So: ask before you try (see
`_create_file` probing `schema_names()`), bound anything unbounded (see
`CONTEXT_TRI_BUDGET`), and keep `restartEngine()` working as the way back.
