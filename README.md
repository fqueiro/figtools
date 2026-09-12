# plotsmith

Matplotlib figure style for research documents, following Kieran Healy's
*Data Visualization* practices: Tol vibrant palette in fixed order, direct
labels hanging in the margin beyond the panel instead of legends (with
collision-avoiding placement and hairline leaders), gridlines only on the
measured axis and only across the data, no frame at all (no spines, no tick
marks), annotated reference lines and bands, Cleveland dot plots over bars,
and a measurable check that no label sits on a line, a marker, or another
label.

## Install

```
uv add git+https://github.com/fqueiro/plotsmith
```

Each project pins the commit it resolved in its `uv.lock`, so the library can
evolve without silently changing the figures of a finished paper; upgrade a
project deliberately with `uv lock --upgrade-package plotsmith`.

## Use

```python
import plotsmith as ps

ps.setup()                      # once per document; base=11 by default
fig, ax = plt.subplots()
ax.plot(years, portugal, label='Portugal')
ax.plot(years, north, label='North')
ps.band(ax, years, lo, hi)      # CI ribbon, inherits the line's colour
ps.refline(ax, 1, 'parity')
ps.label_lines(ax)              # direct labels replace the legend
ps.save(fig, '../results/figures/fig1')
```

`setup` also turns on matplotlib's constrained layout. The direct labels
hang beyond the panel, and the layout engine measures the margin they need
and shrinks the axes to fit, so the panel ends where the data ends and the
grid stops with it. Leave `tight_layout()` out: on such a figure it warns
and switches the engine off. A label wider than a fifth of the figure
width is broken at spaces into two or three balanced lines, decided per
output size; a label you break by hand (with a newline) is kept as
written, and `label_lines(ax, wrap=None)` turns wrapping off.

Two house rules the helpers assume. The caption names the quantity, its
unit and its sign convention, and the reference rule is named on the line
(`refline(ax, 1, 'parity')`), so the axes carry no title that repeats
them; a title stays only where the ticks alone would mislead, as on a
scatter whose two axes are different variables. Limits are left to
autoscale, which includes the reference rule, and a named rule at the top
of the data takes the headroom its label needs; a limit set by hand states
its reason in a comment.

`save` writes `fig1.pdf` at paper width (5.9 in) and
`fig1_slides.pdf` at slide width (4.1 in), re-solving label placement
at each size and refusing to write a variant whose labels collide or whose
text runs off the figure (a y-title taller than the panel is cut without a
word otherwise). Include
the PDFs in LaTeX at natural size, with no `width=` option: the figure was
rendered at the physical size of its destination, so its type lands on the
page at the exact point size `setup` chose. Scaling at `\includegraphics`
time is what un-sizes fonts.

## Tests

```
uv sync
uv run pytest
```

Typography is one knob: `setup(base=...)` puts titles and axis labels at the
base size and every other piece of text (ticks, line labels, key entries,
annotations) half a point below, so elements cannot drift apart and a bigger
deck is `setup(base=13)`, not a retouching session. The default matches
ggplot's 11pt theme base.

Beyond the style: `pointrange` (coefficient profiles, event studies),
`dotplot(..., ci=)` (horizontal coefficient plots), `binscatter`,
`label_scatter` (names as marks, optional OLS guide and N/R² note),
`label_points`, `key`, `shade`, `panels`.

## Check

```
uv run fig-check code                     # every document in code/
uv run fig-check code adoption_lifecycle  # one document
```

Runs each document's code the way its renderer would (the `# %%` cells of a
percent-format `.py`, `.qmd` python chunks, `.ipynb` code cells) and
measures every figure with `collisions`; exits non-zero on any overlap or
any text off the figure; the check replaces eyeballing the rendered
figures. A `.py` counts as a document when Quarto would render it, that is
when it has a `# %% [markdown]` or `# %% [raw]` cell; a plain module has
none and is left alone. Each figure is drawn at the size the html render
gives it, resolved as Quarto resolves `fig-width` and `fig-height`: the
project's `_quarto.yml`, any `_metadata.yml`, and the document's front
matter, with 7 by 5 inches when none sets them. A name that matches no
document, or a folder with none, is an error rather than a clean exit. The
library itself has no document-format dependence, so plain scripts check
their own figures inline with `assert not ps.collisions(fig)`.

## Digest

A figure is a picture of data that a program cannot read back, and a
document written for a human reader states its findings in prose without
describing what each figure shows. So every figure a notebook kernel
displays after `setup()` carries a *digest*, its plotted data as compressed
JSON inside the image's metadata, a PNG's text chunk or a PDF's document
information: per panel, the axis titles and kinds, each series with its
label, colour and values (lines, markers, bars, scatters, bands, intervals,
error bars), reference lines and shaded spans with their labels, named
points and notes. The image is unchanged and nothing else appears in the
cell. The PDFs `save` writes for a paper carry the same digest, so
`render-digest figure results/figures/fig1.pdf` reads a paper figure back
without the document that made it; `figure` takes PNGs and PDFs alike.

After a Quarto render, the digests come back out of the page along with
its tables and printed output. In a project's `_quarto.yml`:

```yaml
project:
  post-render: python -m plotsmith.digest post-render
```

writes, for each rendered document, a folder under
`~/.local/share/render-digests/<project>/<document path>/` holding
`outputs.md` (headings, each figure's values as tables, the page's tables
as markdown, printed output as code blocks, in document order), one CSV
and one PNG per figure, and a manifest with the render time and a hash of
the source. `render-digest where <document>` prints the folder;
`render-digest check <document>` exits 1 when the source changed since the
render, so a reader knows to render again first. The folder lives outside
the repository and outside Dropbox. A document rendered to PDF yields its
figures, and only them, under `pdf/` in the same folder: the LaTeX engine
copies each figure file's digest into the document, and `render-digest
figure` on any such PDF prints them all.

Limits, each stated in the digest itself: series longer than 5,000 points
are thinned evenly; large image grids are given by shape and range only;
colour or size that varies point by point is not decoded. A markdown table
over about 600 cells is thinned to its first, last and extreme rows and
says so; the CSV keeps every row. Calling `set_matplotlib_formats` after
`setup()` replaces the wrapped formatter and drops the digest; call `setup()`
again after it.
