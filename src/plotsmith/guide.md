# plotsmith: how to use it

plotsmith is a matplotlib style and a small set of helpers for research
figures: direct labels in place of legends, a measured check that no label
collides with anything, export at the physical size of the paper and the
slides, and a digest of each figure's data that can be read back as text.
`python -m plotsmith` prints this guide, `python -m plotsmith readme` the
README with the reasons behind each rule, and `help(ps.<function>)` gives
each function's full reference.

## A figure

```python
import matplotlib.pyplot as plt
import plotsmith as ps

ps.setup()                                    # once per document, before any figure

fig, ax = plt.subplots()
for (region,), d in df.sort('year').group_by('region', maintain_order=True):
    ax.plot(d['year'], d['share'], label=region)
    ps.band(ax, d['year'], d['lo'], d['hi'])  # interval of the line just drawn
ps.refline(ax, 0, 'no change')                # a reference rule, named in place
ps.label_lines(ax)                            # direct labels; never ax.legend()
ps.save(fig, '../results/figures/share')      # share.pdf and share_slides.pdf
plt.show()                                    # the cell's last statement
```

Regression coefficients: `ps.dotplot(ax, names, estimates, ci=(lo, hi))`
for a ranked list, `ps.pointrange(ax, x, estimates, lo, hi)` for an event
study or a profile, each with `ps.refline(ax, 0)`.

## Inputs

- Every helper converts its data arguments with `np.asarray(..., float)`,
  so Polars columns (`df['x']`) go in as they are. Nothing in plotsmith
  uses pandas: never convert a frame to plot it.
- Aggregate in Polars before plotting large data. A scatter of a million
  points is slow to draw and makes a heavy PDF, and the digest keeps at most
  5,000 points per series. `ps.binscatter(ax, x, y)` bins the microdata
  itself and fits the slope on every observation.
- `binscatter(..., controls=)` partials out the controls by dense least
  squares, which suits a few continuous controls. Absorb high-dimensional
  fixed effects first (with xhdfe) and pass the residuals.
- A Polars null becomes NaN. `binscatter` raises on any; a line breaks at
  one. Drop or fill missing values explicitly first.

## Rules the helpers assume

- `setup()` turns on constrained layout, which makes room for the labels
  hanging in the margin; never call `tight_layout()`. Calling
  `set_matplotlib_formats` after `setup()` drops the digest; call `setup()`
  again after it.
- No legends, and no axis title that repeats the caption. `label_lines`
  names each line at its end, `label_last` a series drawn without a line,
  and `key` stacks coloured names in a corner where line ends crowd, as in
  small multiples.
- Text sizes come from `setup(base=...)` alone; never set a font size by
  hand.
- Axis limits are left to autoscale; a limit set by hand states its reason
  in a comment. Helpers move a limit only while it autoscales (ending the
  panel at the data for direct labels, raising the top to fit a rule's
  label); a limit set by hand, such as a scale shared across figures, is
  never moved. A rule's label that then does not fit on its usual side goes
  to the other side of the rule, or is left outside the panel for the check
  to report.
- Figures read at one scale, such as subfigures the paper stacks, share one
  size, one axes box and one span of data. Make each with
  `fig = plt.figure(figsize=(w, h), layout='none')` and
  `ax = fig.add_axes([l / w, b / h, 1 - (l + r) / w, 1 - (b + t) / h])`,
  margins in inches wide enough for the tick and direct labels, since the
  layout engine sizes each axes to its own labels. Set every figure's limits
  by hand to windows of the same span, with room inside the axes for rule
  labels near the top. plotsmith keeps the limits and the box, and the check
  refuses a label that does not fit.
- Figures read at one scale, such as subfigures a paper stacks, share one
  size and a fixed axes box: `plt.figure(figsize=..., layout='none')` with
  `fig.add_axes` at margins given in inches, since a layout engine sizes
  each axes to its own labels. Each gets limits set by hand to a window of
  the same span, with room inside the axes for the labels of rules near its
  top.
- In a Quarto or Jupyter document, a captioned figure cell produces the
  figure and nothing else: end it with `plt.show()`. A cell ending on a bare
  `ps.key(...)`, `ps.dotplot(...)` or `ax.set_xlabel(...)` displays the
  return value as a second output, and Quarto splits the figure into
  subfigures with the caption on the wrong one.
- `save` writes the paper PDF (5.9 in wide) and the slides PDF (4.1 in),
  and refuses any size at which labels collide or text runs off the figure.
  Include the PDFs in LaTeX with no `width=`: they already have their final
  size.

## Checking and reading figures

- `uv run fig-check code <document>` runs a document and fails on any
  overlapping or clipped label; a plain script uses
  `assert not ps.collisions(fig)`.
- The check, here and in `save`, measures every text the figure draws,
  including a key packed from offset boxes (`TextArea`s in an
  `AnchoredOffsetbox`, for a key with only some words coloured) or a
  legend, each counted as one label. It tests text against other text,
  the figure's edge, lines (`plot`, `hlines`, `vlines`), scatter markers
  at their drawn size, and the drawn outlines of filled shapes: bars,
  arrows, rectangles, polygons and bands.
- A label may sit wholly inside a shape or clear of it, never across its
  edge, unless it is pinned in that shape: an annotation anchored, or a
  text positioned, in data coordinates inside the shape or on its edge. So
  a value at its bar's end (`ax.bar_label`) or inside its bar passes, and a
  label straddling two bars does not. Shaded spans (`shade`, `axvspan`) and
  reference rules are furniture any label may cross; heatmap cells and
  images are not measured. The placement helpers dodge what the check
  measures.
- With `post-render: python -m plotsmith.digest post-render` in the
  project's `_quarto.yml`, `render-digest where <document>` names the folder
  holding the rendered document's figures as text (`outputs.md`, a CSV per
  figure). `render-digest figure <file>.pdf` reads a saved figure's data
  back.
