# CLAUDE.md

Shared matplotlib figure library used by every research project via a git
dependency (`uv add git+https://github.com/fqueiro/fig-style`). README.md
holds the user-facing rationale; this file holds working rules.

- The library is `src/fig_style/__init__.py` (style, labelling engine, plot
  helpers, export) plus `src/fig_style/check.py` (the `fig-check` CLI).
  Single module by design: the placement machinery is tightly coupled and
  the public API is small.
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
