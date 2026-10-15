# CLAUDE.md

figtools, a public matplotlib library on PyPI that the research projects
depend on, each pinning a version in its `uv.lock`. README.md gives the
reasons behind each rule, CONTRIBUTING.md the setup, tests and release
steps; this file holds the working rules.

- The library is `src/figtools/__init__.py` (style, labelling engine, plot
  helpers, export), `src/figtools/check.py` (the `fig-check` CLI) and
  `src/figtools/digest.py` (the figure digest and the `render-digest` CLI).
  The style module is one file by design: the placement machinery is
  tightly coupled and the public API is small.
- `src/figtools/guide.md`, printed by `python -m figtools`, is the page
  sessions in other projects read before writing code with the library.
  It says what to do and README.md why, one home per rule; a change to a
  public name, signature, default or rule rewrites both in the same commit.
- A change that alters figures (sizes, palette, placement, defaults, what
  `collisions` reports) or breaks code gets its `CHANGELOG.md` entry in the
  same commit, and its reason in the docstring or comment where the value
  lives.
- The gallery (`docs/gallery`, `docs/readme_figure.py`) runs in the tests
  and must stay clean; a change that alters figures regenerates the PNGs by
  running the scripts, and each is looked at before committing.
- The digest is a contract with the projects' render step (`digest.py`'s
  docstring has the mechanism): a new artist type or helper mark that
  `describe` learns to read gets a test that checks the values and labels
  that come back, and `markdown` and `csv_rows` learn to write it.
- Run `uv run pytest` before committing. A release is a pushed tag
  `vX.Y.Z` after `uv version X.Y.Z` (CONTRIBUTING.md); never tag or
  publish unasked, since PyPI never takes a version back.
- Label placement is measured: a change to the engine keeps `collisions`
  honest, and a new label-producing helper takes part in placement or is
  exempted from the check (as `label_scatter` names are) with the reason
  stated.
- Typography is two sizes derived from `setup(base=...)`, plus the scatter
  names of `label_points` and `label_scatter` two points below. Never
  hard-code a font size in a helper; resolve through `_text_size()`.
- The grey background curve is `refcurve` (the digest calls its series role
  "guide"), private state on matplotlib objects uses the `_figtools_`
  prefix, and drawing helpers return a `_Drawn` list, which notebooks
  display as nothing.
