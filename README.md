# plotsmith

Matplotlib figure style for research documents, following Kieran Healy's
*Data Visualization* practices: Tol vibrant palette in fixed order, direct
labels instead of legends (with collision-avoiding placement and hairline
leaders), gridlines only on the measured axis, no frame at all (no spines,
no tick marks), annotated reference lines and bands, Cleveland dot plots
over bars, and a measurable check that no label sits on a line, a marker,
or another label.

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

`save` writes `fig1.pdf` at paper width (5.9 in) and
`fig1_slides.pdf` at slide width (4.1 in), re-solving label placement
at each size and refusing to write a variant whose labels collide. Include
the PDFs in LaTeX at natural size, with no `width=` option: the figure was
rendered at the physical size of its destination, so its type lands on the
page at the exact point size `setup` chose. Scaling at `\includegraphics`
time is what un-sizes fonts.

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
uv run fig-check code                     # every .qmd in code/
uv run fig-check code adoption_lifecycle  # one document
```

Runs each document's code the way its renderer would (`.qmd` python chunks,
`.ipynb` code cells) and measures every figure with `collisions`; exits
non-zero on any overlap; the check replaces eyeballing the
rendered figures. The library itself has no document-format dependence, so
plain scripts check their own figures inline with
`assert not ps.collisions(fig)`.

## Tests

```
uv sync
uv run pytest
```
