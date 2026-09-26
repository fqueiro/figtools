# CLAUDE.md

Shared matplotlib figure library used by every research project via a git
dependency (`uv add git+https://github.com/fqueiro/plotsmith`). README.md
holds the user-facing rationale; this file holds working rules.

- The library is `src/plotsmith/__init__.py` (style, labelling engine, plot
  helpers, export), `src/plotsmith/check.py` (the `fig-check` CLI) and
  `src/plotsmith/digest.py` (the figure digest and the `render-digest` CLI).
  The style module is one file by design: the placement machinery is
  tightly coupled and the public API is small.
- `src/plotsmith/guide.md`, printed by `python -m plotsmith`, is the page
  sessions in other projects read before writing code with the library: a
  change to a public signature, a default or a house rule rewrites it in the
  same commit.
- The digest is a contract with the projects' render step (`digest.py`'s
  docstring has the mechanism): a new artist type or helper mark that
  `describe` learns to read gets a test that draws it and checks the values
  and labels that come back, and `markdown` and `csv_rows` learn to write it.
- Run `uv run pytest` before committing; every commit may be pinned by a
  downstream project, so a broken commit can end up in a lockfile.
- Downstream projects pin commits in their `uv.lock`. Changing defaults
  (sizes, palette, target widths) changes their figures only when they
  upgrade, so changes are allowed, but make them deliberately and record
  the why in the docstring or comment where the value lives.
- Label placement is measured, not assumed: any change to the engine must
  keep `collisions` honest, and new label-producing helpers must either
  participate in placement or be explicitly exempted (as `label_scatter`
  marks are) with the reason stated.
- Typography is two sizes derived from `setup(base=...)`. Never hard-code a
  font size in a helper; resolve through `_text_size()` so everything moves
  with the base.
