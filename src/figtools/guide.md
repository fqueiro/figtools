# figtools: how to use it

figtools is a matplotlib style with helpers for research figures: direct
labels in place of legends, a check that no label collides with anything,
export at the physical size of a paper and of a slide, and a digest of each
figure's data embedded in the file. This guide says what to do.
`python -m figtools readme` prints the README, which gives the reasons, and
`help(ft.<function>)` gives each function's full reference.

## A figure

```python
import matplotlib.pyplot as plt
import figtools as ft

ft.setup()                                    # once per document, before any figure

fig, ax = plt.subplots()
for (region,), d in df.sort('year').group_by('region', maintain_order=True):
    ax.plot(d['year'], d['share'], label=region)
    ft.band(ax, d['year'], d['lo'], d['hi'])  # interval of the line just drawn
ft.refline(ax, 0, 'no change')                # a reference rule, named in place
ft.label_lines(ax)                            # direct labels; never ax.legend()
ft.save(fig, 'share')                         # share.pdf and share_slides.pdf
plt.show()                                    # the cell's last statement
```

`df` is a Polars frame here. With pandas, loop over
`df.sort_values('year').groupby('region', sort=False)` and unpack
`region, d`.

Regression coefficients:

- An event study or a profile: `ft.pointrange(ax, x, est, lo, hi)`, with
  `ft.refline(ax, 0)` for the zero rule and
  `ft.refline(ax, date, 'reform', axis='x')` at the treatment date.
- A ranked list: `ft.dotplot(ax, names, est, ci=(lo, hi))`, with
  `ft.refline(ax, 0, axis='x')`. A dot plot's values lie along x, so its
  zero rule is vertical.

## Inputs

- Every helper converts its data with `np.asarray(..., float)`, so NumPy
  arrays, lists, pandas Series and Polars columns go in as they are.
- Aggregate large data before plotting. A scatter of a million points is
  slow to draw and makes a heavy PDF, and the digest keeps at most 5,000
  points per series. `ft.binscatter(ax, x, y)` bins the microdata itself
  and fits the slope on every observation.
- `binscatter` cuts bins at quantiles of x and keeps tied values in one
  bin, so `bins` is a maximum and a discrete x gets one bin per value.
  `controls=` partials the controls out of x and y by dense least squares,
  which suits a few continuous controls; absorb high-dimensional fixed
  effects first and pass the residuals. Its docstring says when the
  residualised bin means misstate the shape of E[y | x, w].
- A missing value (NaN, a pandas NA, a Polars null) is NaN to the helpers:
  `binscatter` raises on any, and a line breaks at one. Drop or fill
  missing values first.

## Rules the helpers assume

- `setup()` turns on constrained layout, which makes room for the labels
  hanging in the margin; never call `tight_layout()`. In code that shares
  a process with other plotting, use `with ft.style():` instead, which
  undoes the style on leaving. Calling `set_matplotlib_formats` after
  `setup()` drops the digest; call `setup()` again after it.
- No legends, and no axis title that repeats the caption. `label_lines`
  names each line at its end; call it once per axes, after plotting every
  series, since a second call raises. `label_last` names a series drawn
  without a line, and `key` stacks coloured names in a corner where line
  ends crowd, as in small multiples (name the series once, in the first
  panel).
- Series take the seven colours of `ft.PALETTE` in order. `label_lines`
  and `key` raise when two series they name cannot be told apart, so past
  seven series give each line its own colour, dash pattern or marker, and
  give each `key` entry its own colour.
- Text sizes come from `setup(base=...)` alone; pass no font size by hand.
  For slides, raise the base.
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
  labels near the top. figtools keeps the limits and the box, `save` writes
  each figure at the same size, and the check refuses a label that does not
  fit.
- In a Quarto or Jupyter document, a captioned figure cell produces the
  figure and nothing else: end it with `plt.show()`, a drawing helper or
  `save`, and give tables cells of their own. A cell ending on a bare
  matplotlib call such as `ax.set_xlabel(...)`, or on `panels` or
  `collisions`, displays its return value as a second output, and Quarto
  splits the figure into subfigures with the caption on the wrong one.
- `save(fig, path)` writes `path.pdf` 5.9 in wide and `path_slides.pdf`
  4.1 in wide, uncropped, so figures built alike come out the same size,
  and refuses any size at which labels collide or text runs off the
  figure. Give `path` without an extension. `widths={'poster': 9.0}` also
  writes `path_poster.pdf`, and `widths={'paper': 6.2}` changes a default
  width; `targets=('poster',)` writes only the targets named, and must
  name every target `widths` gives. Include the PDFs in LaTeX with no
  `width=`: they already have their final size.
- The drawing helpers return what they drew in a list, for restyling:
  `rule, label = ft.refline(ax, 0, 'zero')`,
  `stems, dots = ft.dotplot(ax, names, est)`,
  `xm, ym, slope = ft.binscatter(ax, x, y)`. A notebook displays the list
  as nothing.
- The digest embeds every plotted point, a scatter's observations
  included. For confidential data use `ft.setup(digest=False)`, or
  `ft.save(fig, path, digest=False)` for one figure.

## Checking and reading figures

- `assert not ft.collisions(fig)` checks a figure in a script; `save` runs
  the same check at each size it writes.
- `fig-check <folder>` runs every document in the folder (percent-format
  `.py`, `.qmd`, `.ipynb`) and checks each figure; `fig-check <folder>
  <document>` runs one, named by its stem or its file name. It exits 0 when
  every figure is clean, 1 on overlaps, 2 when a document raised an error.
  It needs `pip install "figtools[check]"`, and runs each document in the
  folder Quarto would, so it can be called from anywhere.
- The check measures every text the figure draws, including a key packed
  from offset boxes or a legend, each counted as one label. It tests text
  against other text, the figure's edge, lines (`plot`, `hlines`,
  `vlines`), scatter markers at their drawn size, and the drawn outlines of
  filled shapes: bars, arrows, rectangles, polygons and bands.
- A label may sit wholly inside a shape or clear of it, never across its
  edge, unless it is pinned in that shape: an annotation anchored, or a
  text positioned, in data coordinates inside the shape or on its edge. So
  a value at its bar's end (`ax.bar_label`) or inside its bar passes, and a
  label straddling two bars does not. Shaded spans (`shade`, `axvspan`) and
  reference rules are furniture any label may cross; heatmap cells and
  images are not measured. The names `label_scatter` prints may overlap one
  another, but any other label over one is reported. The placement helpers
  steer clear of what the check measures.
- With `post-render: python -m figtools.digest post-render` in the
  project's `_quarto.yml`, `render-digest where <document>` names the folder
  holding the rendered document's figures as text (`outputs.md`, a CSV per
  figure), under `~/.local/share/render-digests` or `FIGTOOLS_DIGEST_DIR`.
  The hook reads figures linked beside the page as well as embedded ones,
  so `embed-resources` is not needed, and skips pages made from plain
  `.md`. `render-digest figure <file>.pdf` reads a saved figure's data
  back.
