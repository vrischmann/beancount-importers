# AGENTS.md

Project-specific instructions for AI coding agents working on beancount-importers.

## Project rules

- The code lives in three flat top-level packages (`beancount_ccm`, `beancount_fortuneo`, `beancount_helpers`), each a single `__init__.py`; they import each other by absolute name (`from beancount_helpers import ...`) because there is no shared parent package.
- Everything under `synthetic/` is committed but **generated**: edit `generate_sample.py` in the fixture directory and re-run `uv run generate_sample.py` from inside it, then commit the regenerated CSV, zip and beancount files. Never hand-edit the fixture data files.
- Keep fixture files byte-faithful to the real bank exports: the CCM CSV is ISO-8859-15 with CRLF endings and French decimal commas, the Fortuneo Bourse CSV is ISO-8859-1 — never UTF-8. The exact format of each fixture is specified in that directory's `README.md`; read it before touching anything there.
- The generators read `FIELDS` from the importers so that `identify()` keeps passing; changing an importer's header fields therefore requires regenerating its fixture in the same change.
- The CCM generator also imports `beancount_ccm` and writes the importer's current output to `current_output.beancount`, so regenerating after an output change refreshes the ground truth automatically. The Fortuneo ground truth `expected.beancount` is written by hand — update it deliberately.
- `synthetic/fortuneo-bourse/expected.beancount` is both test ground truth and a miniature ledger (the `commodity`/`open` directives the tests load); it must stay `bean-check`-clean, so treat edits to it as code changes.
- Fortuneo security resolution must never guess between two candidate commodities: a wrong ticker leaves the ledger balanced, so nothing downstream catches it. That is why the `AMBIGUITY_MARGIN` guard exists in `beancount_fortuneo/__init__.py` — keep it, and keep `fuzzy=False` as the default. The matching tiers and their rationale are documented under "Resolving a security from the broker label" in the repo README.
- The importers stay thin format importers: no payee inference and no smart-importer logic, since that belongs to the user's ledger project.
- Formatting is yapf with `column_limit = 300` (see `.style.yapf`); do not reformat to PEP 8 line lengths.
- A new top-level package must be registered under `[tool.hatch.build.targets.wheel] packages` in `pyproject.toml`, or it will not ship in the wheel.

## Commands

```
just test [args...]   # uv run pytest [args...]
```

When calling tools directly, use the same arguments as the corresponding recipe.

## Testing

- `just test` runs the whole suite (44 tests): no build tags, no required env vars, no opt-in tests. There is no CI, so a local run is the only gate.
- The Fortuneo acceptance test (`test_extract_matches_ground_truth` in `tests/test_fortuneo_stock.py`) combines `expected.beancount` with the importer's raw output and shells out to `bean-check`. The binary ships with beancount and is on PATH under `uv run`.
- The per-fixture `README.md` files document each file's role, the expected treatment per row type, and the noise cluster that must stay skipped.

## Final validation

```
just test
```
