"""Shared figure style for research documents.

One call to `setup()` at the top of a document and every figure in it matches
every figure in every other project that uses this package: the style, the
direct-labelling machinery that replaces legends, the small set of plot types
the work actually uses (time series, dot plots, pointranges, binned and
labelled scatters), the per-destination export, and the measurement that
keeps direct labelling honest.

The choices are inspired by Kieran Healy's *Data Visualization*:

  * One colour-blind-safe categorical palette (Tol vibrant), assigned in a
    fixed order and never cycled, so a colour means the same entity
    series-wide.
  * Direct labelling in place of legends. A legend forces a reader to hold a
    colour in memory and hop between the key and the line; a label at the end
    of the line does not. `label_lines`, `label_points` and `label_last` do
    this, and `key` is the fallback for the figure that cannot. Line labels
    hang in the figure margin beyond the panel, the way ggplot draws them
    with clipping off: the panel stays the data region, and the layout
    engine reserves the space the labels need rather than the axis being
    stretched to hold them.
  * Gridlines only on the axis carrying the measurement, drawn light and
    behind the data, so they support reading values without competing; they
    stop where the data stops instead of running on under the labels.
  * Ticks that name values the data can take: where everything plotted
    against an axis is a whole number, so are its ticks, since matplotlib's
    default steps would otherwise label a year axis 2007.5, 2010.0, 2012.5.
  * No frame around the data at all: no spines, no tick marks. The grid and
    the floating tick labels carry every reference the reader needs; axis
    lines are frame ink.
  * Reference lines and shaded bands annotated in place ("parity", "fragile
    data") rather than left for the reader to infer.
  * The caption names the quantity, its unit and its sign convention, and
    the reference rule is named on the line, so the axes carry no title
    that repeats them; a title stays only where the ticks alone would
    mislead, as on a scatter whose two axes are different variables. Limits
    are left to autoscale, which includes the reference rule; a limit set
    by hand states its reason in a comment, and no helper moves it.
  * Two type sizes derived from one base (`setup(base=...)`): titles and
    axis labels at the base, every other piece of text half a point below.
    One knob makes size drift between elements structurally impossible, and
    turns the slide version of a figure into a larger base rather than a
    retouching session.
  * Figures leave the document at the physical size of their destination
    (`save`): the paper column, the slide slot. Included at natural size,
    type lands on the page at its true point size; scaling at
    includegraphics time is what un-sizes fonts.

Position encodes value everywhere in this series; where a comparison is of
magnitudes across categories, `dotplot` draws Cleveland dot plots instead of
bars, which spend ink on a length the reader does not need.

Direct labelling only works if the labels can be read, so the labels place
themselves: each is moved clear of the others, of the plotted lines and of
the markers, with a hairline leader once it has moved far enough that the
pairing stops being obvious, and a label too wide for the figure is broken
into two or three lines so the panel keeps its width. That has to happen
when the figure is drawn,
because it depends on how big the panel turned out to be, so it runs from a
draw callback and recomputes on every draw rather than being decided when the
label is created. `collisions` measures a drawn figure for what is left; it
is the check to run on a figure rather than squinting at it.

A figure displayed in a notebook, and every PDF `save` writes, carries a
digest of its plotted data inside the image file (`digest`), so a reader that
cannot see the image, a program or a language model working from the
rendered document or the paper, can still read the figure: its series,
values, rules and annotations, and after a render the document's tables and
printed output beside them.
"""

import pickle
from functools import partial
from itertools import combinations, pairwise
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.transforms import Bbox
from numpy.typing import ArrayLike

__all__ = [
    'BASE_SIZE',
    'GREY',
    'PALETTE',
    'PALETTE_LONG',
    'TARGET_WIDTHS',
    'band',
    'binscatter',
    'collisions',
    'dotplot',
    'grid_axis',
    'guide',
    'key',
    'label_last',
    'label_lines',
    'label_points',
    'label_scatter',
    'load',
    'panels',
    'pointrange',
    'refline',
    'save',
    'setup',
    'shade',
]

# Neutral greys for non-data ink. Nothing structural is drawn in a palette
# colour, so colour always means "this is a data series".
GREY = {'rule': '0.45', 'grid': '0.88', 'text': '0.35', 'shade': '0.93'}

# Fixed categorical colour order (Paul Tol's vibrant scheme, CVD-safe),
# assigned to entities in fixed order, never cycled; at most four series per
# chart. Blue-then-red keeps the temperament of the published AER figures
# this style descends from while staying colour-blind safe; Tol's grey is
# excluded because grey is the furniture channel here. Projects define their
# own semantic maps (which entity gets which colour) on top of these; the
# package owns only the colours and the fixed order.
PALETTE = ['#0077BB', '#CC3311', '#009988', '#EE7733']

# ggplot's theme base size. Titles and axis labels sit at the base and every
# other piece of text half a point below (see setup); matplotlib's own 10pt
# default reads small in a paper column or on a projected slide.
BASE_SIZE = 11.0


def _text_size():
    """The running text size (ticks, labels, annotations), from the live
    rcParams, so helper defaults track whatever `setup(base=...)` set."""
    size = mpl.rcParams['xtick.labelsize']
    try:
        return float(size)
    except (TypeError, ValueError):
        raise RuntimeError('plotsmith helpers need setup() to have run '
                           f'(tick label size is {size!r})') from None


# The rest of Tol vibrant (cyan, magenta) plus black, for the occasional
# figure with more series than the four-colour working palette holds. Past
# four categories colour stops doing the identifying on its own and the
# direct labels carry it, so these are a way to keep the lines
# distinguishable, not a way to name more entities.
PALETTE_LONG = [*PALETTE, '#33BBEE', '#EE3377', '#000000']


def _axis_data(axis):
    """Every value plotted against `axis`, artist by artist.

    Only artists drawn in data coordinates count. The rules from
    `axhline`/`axvline` are drawn in a blended transform (see `_data_lines`)
    and shaded bands are patches, so furniture at 2020.5 is not evidence
    that the axis carries halves. Lines and collections are what this reads;
    an axes built from anything else (bars, histograms) returns nothing and
    is left alone.
    """
    ax = axis.axes
    i = 0 if axis.axis_name == 'x' else 1
    out = []
    for ln in ax.lines:
        if ln.get_transform() is ax.transData:
            out.append(ln.get_xdata(orig=False) if i == 0
                       else ln.get_ydata(orig=False))
    for coll in ax.collections:
        if coll.get_offset_transform() is ax.transData:
            off = np.ma.getdata(coll.get_offsets())
            if np.ndim(off) == 2 and len(off):
                out.append(off[:, i])
        if coll.get_transform() is ax.transData:
            out += [p.vertices[:, i] for p in coll.get_paths() if len(p.vertices)]
    return out


def _all_whole(arrays):
    """True when every value in `arrays` is a whole number, and there is at
    least one to judge by."""
    seen = False
    for a in arrays:
        a = np.asarray(np.ma.getdata(a), float).ravel()
        a = a[np.isfinite(a)]
        if not a.size:
            continue
        if not np.array_equal(a, np.round(a)):
            return False
        seen = True
    return seen


class _WholeNumberLocator(mpl.ticker.AutoLocator):
    """Matplotlib's default tick locator, refusing fractional ticks on
    whole-number data.

    The default tick steps include 2.5, so a sixteen-year axis in a panel
    with room for seven ticks is labelled 2007.5, 2010.0, 2012.5: positions
    no observation can occupy. Which side of that a figure falls on is
    decided by how much margin the direct labels take, so one longer country
    name flips a panel into decimals. Where every value plotted against the
    axis is a whole number, so are its ticks: years, ages, counts, quintiles.

    The data are read only when the default ticks came out fractional, which
    is the rare case, so a figure that was never at risk pays nothing.
    """

    def tick_values(self, vmin, vmax):
        ticks = super().tick_values(vmin, vmax)
        if np.array_equal(ticks, np.round(ticks)):
            return ticks
        axis = getattr(self, 'axis', None)
        if axis is None or not _all_whole(_axis_data(axis)):
            return ticks
        self.set_params(integer=True)
        try:
            return super().tick_values(vmin, vmax)
        finally:
            self.set_params(integer=False)


class _LinearScale(mpl.scale.LinearScale):
    """The linear scale with `_WholeNumberLocator` for its default ticks.

    Registered under matplotlib's own name for the scale, which is what
    carries the rule to every axes a document draws, whether it came from
    `panels`, `plt.subplots` or a library plotting into an axes of its own,
    without anything having to ask for it. An axis given fixed ticks (a
    categorical axis) or a unit converter (dates) replaces the locator and
    is unaffected.
    """

    name = 'linear'

    # Matplotlib up to 3.10 hands every scale its axis on construction. From
    # 3.11 it reads the constructor's signature, leaves the axis out when the
    # constructor does not ask for it, and warns when it does (the argument
    # goes in 3.15), so the signature follows the installed version.
    if mpl.__version_info__[:2] >= (3, 11):
        def __init__(self):
            super().__init__()
    else:
        def __init__(self, axis):
            super().__init__(axis)

    def set_default_locators_and_formatters(self, axis):
        super().set_default_locators_and_formatters(axis)
        axis.set_major_locator(_WholeNumberLocator())


def setup(base=BASE_SIZE):
    """Apply the figure style. Call once per document.

    `base` is the one typographic parameter. Titles and axis labels are set
    at `base`; ticks, line labels, key entries and annotations all sit half
    a point below it, resolved from the live rcParams by every helper, so
    the two sizes move together and nothing can drift apart element by
    element. Scale a whole document (a slide deck, a poster) by changing
    `base`, never by touching individual elements.

    In a notebook kernel this also arms the figure digest (`digest.install`):
    every figure the kernel displays from here on carries its plotted data
    inside its PNG. It runs before the style because resolving the inline
    backend may apply the kernel's own rc settings, and the style below has
    the last word.
    """
    # Imported here, not at the top: `python -m plotsmith.digest` would
    # otherwise run a module the package had already imported.
    from . import digest
    digest.install()
    # Whole-number ticks on whole-number data (see `_WholeNumberLocator`).
    # There is no rcParam for the default locator, so the rule travels on
    # the scale every axes is created with.
    mpl.scale.register_scale(_LinearScale)
    text = base - 0.5
    plt.rcParams.update({
        # Sizing. 110 dpi renders crisply in the HTML documents without
        # inflating them; savefig is higher for anything pulled into a paper.
        'figure.dpi': 110,
        'savefig.dpi': 200,
        'savefig.bbox': 'tight',

        # Layout. Constrained: the direct labels hang beyond the panel, and
        # the layout engine measures the margin they need and shrinks the
        # axes to fit, so a figure leaves at its target width with every
        # label inside it (without an engine they would run off the figure).
        # It re-runs on every draw, so `save` re-solving at another size
        # still fits. A `tight_layout()` call on such a figure warns and
        # switches the engine off: drop the call instead.
        'figure.constrained_layout.use': True,

        # Type. One family, two sizes: the reader should not have to decode
        # a typographic hierarchy on top of the data.
        'font.size': base,
        'axes.titlesize': base,
        'axes.labelsize': base,
        'xtick.labelsize': text,
        'ytick.labelsize': text,
        'legend.fontsize': text,
        # Titles sit flush left, where reading starts, rather than centred
        # over the data.
        'axes.titlelocation': 'left',
        'axes.titlepad': 6.0,

        # Frame. None: the grid and the floating tick labels carry every
        # reference the reader needs, so spines are frame ink (the look of
        # the published AER figures this style descends from). The edge
        # colour stays set for the figure that turns a spine back on.
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.spines.left': False,
        'axes.spines.bottom': False,
        'axes.edgecolor': GREY['rule'],
        'axes.linewidth': 0.8,
        'axes.labelcolor': 'black',

        # Grid. Horizontal only by default (these are time series, read
        # against the vertical scale); `grid_axis` flips it for ranked
        # horizontal bars and dot plots.
        'axes.grid': True,
        'axes.grid.axis': 'y',
        'axes.axisbelow': True,
        'grid.color': GREY['grid'],
        'grid.linewidth': 0.6,
        'grid.alpha': 1.0,

        # Ticks. No marks, only labels: with no spine to hang from, marks
        # would float. Colour stays set for the figure that turns them
        # back on.
        'xtick.color': GREY['rule'],
        'ytick.color': GREY['rule'],
        'xtick.labelcolor': 'black',
        'ytick.labelcolor': 'black',
        'xtick.major.size': 0.0,
        'ytick.major.size': 0.0,
        'xtick.minor.size': 0.0,
        'ytick.minor.size': 0.0,
        'xtick.major.width': 0.8,
        'ytick.major.width': 0.8,

        # Data ink.
        'lines.linewidth': 1.8,
        'lines.solid_capstyle': 'round',
        'patch.linewidth': 0,
        'axes.prop_cycle': mpl.cycler(color=PALETTE),

        # Legends, for the rare figure that still needs one (more than four
        # series, or series that end stacked on top of each other).
        'legend.frameon': False,
        'legend.handlelength': 1.4,
        'legend.borderaxespad': 0.2,
    })


def grid_axis(ax, axis):
    """Draw the grid on `axis` only ('x', 'y' or 'both')."""
    ax.grid(False)
    ax.grid(True, axis=axis, color=GREY['grid'], linewidth=0.6, alpha=1.0)
    ax.set_axisbelow(True)


def _plotted_lines(ax):
    """Data lines on `ax`: those given a label, in draw order.

    Reference lines from `axhline`/`axvline` and helper artists carry no
    label (matplotlib prefixes theirs with '_'), so labelling by opting in is
    what separates data from furniture.
    """
    return [ln for ln in ax.get_lines()
            if ln.get_label() and not ln.get_label().startswith('_')]


def _data_lines(ax):
    """Every line drawn in data space, whether or not it carries a label.

    What a label must not sit on is any plotted line, and a single-series
    panel never labels its line: there is nothing to tell it apart from. The
    rules from `axhline`/`axvline` are drawn in a blended transform instead,
    which is what separates data from furniture here.
    """
    return [ln for ln in ax.get_lines()
            if ln.get_transform() is ax.transData and len(ln.get_xdata()) > 1
            and not getattr(ln, '_fs_guide', False)]


def guide(ax, x: ArrayLike, y: ArrayLike, lw=1.0, **kw):
    """Draw a background reference curve: a fit, a 45-degree line, a bound.

    Grey and behind the series, because it summarises or frames the data
    rather than being one of the series, and marked as furniture so the
    labelling machinery does not treat it as a line to dodge. A label may sit
    across it; that is what makes it a guide and not a series.
    """
    ln, = ax.plot(x, y, color=GREY['rule'], lw=lw, zorder=1, **kw)
    ln._fs_guide = True
    return ln


def _spread(ys, gap, lo=0.0, hi=1.0):
    """Separate labels stacked too close, moving each as little as possible.

    Returns positions in the input order that keep the vertical order of the
    series, sit at least `gap` apart, and stay inside [lo, hi]. A label with
    clear space around it does not move at all: the crowded runs are found
    by pooling adjacent violators (isotonic regression on the slack
    y - k*gap), and each run spreads centred on its own members. The
    alternative, pushing collisions apart and re-centring the whole block,
    dragged every label in the margin off its line whenever a cluster
    anywhere in the panel needed spreading, sprouting leaders on labels
    whose own space was never contested.
    """
    ys = list(ys)
    if len(ys) == 1:
        return ys
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    # Fall back to an even split when the labels cannot all fit at `gap`.
    gap = min(gap, (hi - lo) / (len(ys) - 1))
    z = [ys[i] - k * gap for k, i in enumerate(order)]
    blocks = []   # [sum, count] per run, fitted means kept increasing
    for v in z:
        blocks.append([v, 1])
        while (len(blocks) > 1 and blocks[-2][0] / blocks[-2][1]
               >= blocks[-1][0] / blocks[-1][1]):
            s, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += n
    fit = [s / n for s, n in blocks for _ in range(n)]
    out = [f + k * gap for k, f in enumerate(fit)]
    # Slide back inside the panel from each end; the gap cap above means the
    # ladder fits, so neither pass can undo the other.
    out[0] = max(out[0], lo)
    for k in range(1, len(out)):
        out[k] = max(out[k], out[k - 1] + gap)
    out[-1] = min(out[-1], hi)
    for k in range(len(out) - 2, -1, -1):
        out[k] = min(out[k], out[k + 1] - gap)
    res = list(ys)
    for k, i in enumerate(order):
        res[i] = out[k]
    return res


def _to_axes(ax, pts):
    """Data coordinates -> axes fraction, honouring log and inverted scales."""
    pts = np.atleast_2d(np.asarray(pts, float))
    return ax.transAxes.inverted().transform(ax.transData.transform(pts))


def _plotted(ax, xy):
    """A point given in data units (numbers, category names, dates) as the
    numbers it is plotted at."""
    x, y = xy
    return (float(np.ravel(ax.convert_xunits(x))[0]),
            float(np.ravel(ax.convert_yunits(y))[0]))


def _to_data(ax, pts):
    """Axes fraction -> data coordinates."""
    pts = np.atleast_2d(np.asarray(pts, float))
    return ax.transData.inverted().transform(ax.transAxes.transform(pts))


def _curve(ax, line, step=0.004):
    """A line's path in axes fraction, resampled fine enough to test against.

    Testing a label box against the plotted points alone would miss a steep
    segment that crosses the box between two observations, which is exactly
    where a line and a label are most likely to collide. The points are
    read where they are plotted, so a line against category names or dates
    is measured at its positions on the axis, not at the names or dates.
    """
    xd = np.asarray(line.get_xdata(orig=False), float)
    yd = np.asarray(line.get_ydata(orig=False), float)
    ok = np.isfinite(xd) & np.isfinite(yd)
    if not ok.any():
        return np.empty((0, 2))
    pts = _to_axes(ax, np.column_stack([xd[ok], yd[ok]]))
    pts = pts[np.isfinite(pts).all(axis=1)]
    # A marker-only series (a scatter drawn through `plot`, or dotted
    # observations) occupies its markers and nothing between them. Joining
    # them up would invent segments that were never drawn and push labels
    # away from space that is in fact empty.
    if line.get_linestyle() in ('None', ' ', '') or not line.get_linewidth():
        return pts
    return _densify(pts, step)


def _densify(pts, step=0.004):
    """Resample a polyline (axes fraction) finely enough to test against."""
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in pairwise(pts):
        n = max(1, int(np.ceil(float(np.hypot(*(b - a))) / step)))
        out.append(a + np.linspace(0, 1, n + 1)[1:, None] * (b - a))
    return np.vstack(out)


def _collection_curves(ax):
    """Strokes drawn as collections, in axes fraction: dot-plot stems and
    dumbbell connectors (`hlines`), pointrange intervals (`vlines`). They
    are data ink like any plotted line, but they live in `ax.collections`,
    which the Line2D-based obstacle tests do not see."""
    out = []
    for coll in ax.collections:
        if not isinstance(coll, mpl.collections.LineCollection):
            continue
        for seg in coll.get_segments():
            pts = _to_axes(ax, np.asarray(seg, float))
            pts = pts[np.isfinite(pts).all(axis=1)]
            if len(pts) >= 2:
                out.append(_densify(pts))
    return out


def _rule_curves(ax):
    """Reference rules (`axhline`/`axvline`) as paths in axes fraction.

    Rules are furniture a short direct label may sit across, so the label
    placements ignore them; a key block parked across one reads as clutter,
    so the key's corner search does not."""
    inv = ax.transAxes.inverted()
    out = []
    for ln in ax.get_lines():
        if ln.get_transform() is ax.transData:
            continue
        xy = np.column_stack([np.asarray(ln.get_xdata(orig=False), float),
                              np.asarray(ln.get_ydata(orig=False), float)])
        if len(xy) < 2:
            continue
        pts = inv.transform(ln.get_transform().transform(xy))
        pts = pts[np.isfinite(pts).all(axis=1)]
        if len(pts) >= 2:
            out.append(_densify(pts))
    return out


def _pad_box(box, pad):
    return Bbox.from_extents(box.x0 - pad, box.y0 - pad,
                             box.x1 + pad, box.y1 + pad)


def _name(artist):
    """An artist's label as the figure's reader would know it; 'unlabelled'
    for matplotlib's own '_'-prefixed ones."""
    lab = str(artist.get_label() or '')
    return 'unlabelled' if not lab or lab.startswith('_') else lab


def _markers(ax):
    """Scatter markers at their drawn size, one entry per scatter: (name,
    whether the axes clip it, centres (N, 2), boxes (N, 4) as x0, y0, x1,
    y1), all in axes fraction.

    Scatters and dot plots draw their data as a collection rather than a
    line, so testing lines alone would let a label land on a point. A
    marker's size is its area in points squared, and its path is drawn at
    the square root of that in points, so a large dot is measured large.
    """
    ap = ax.get_window_extent()
    unit = ax.figure.dpi / 72
    out = []
    for coll in ax.collections:
        if not isinstance(coll, mpl.collections.PathCollection) \
                or not coll.get_visible():
            continue
        off = np.asarray(coll.get_offsets(), float)
        if off.size == 0:
            continue
        k = np.arange(len(off))
        px = coll.get_offset_transform().transform(off)
        ext = np.array([p.get_extents().extents for p in coll.get_paths()])
        ext = ext[k % len(ext)]
        sizes = np.asarray(coll.get_sizes(), float)
        # With no sizes the paths are drawn as they are, in pixels.
        scale = (np.sqrt(sizes[k % len(sizes)]) * unit if len(sizes)
                 else np.ones(len(off)))[:, None]
        box = np.column_stack([px, px]) + ext * scale
        ok = np.isfinite(box).all(axis=1)
        origin = np.array([ap.x0, ap.y0, ap.x0, ap.y0])
        span = np.array([ap.width, ap.height, ap.width, ap.height])
        out.append((_name(coll), coll.get_clip_on(),
                    (px[ok] - origin[:2]) / span[:2],
                    (box[ok] - origin) / span))
    return out


def _marker_boxes(ax, size=3.0):
    """Boxes around plotted markers, in axes fraction, at their drawn size
    and reaching at least `size` points from each centre, for placement to
    dodge. The floor keeps small markers from being passed too closely;
    the drawn size keeps a large dot from being passed at all, which
    `collisions` would refuse."""
    ap = ax.get_window_extent()
    ux = size * ax.figure.dpi / 72 / ap.width
    uy = size * ax.figure.dpi / 72 / ap.height
    return [Bbox.from_extents(min(x0, cx - ux), min(y0, cy - uy),
                              max(x1, cx + ux), max(y1, cy + uy))
            for _, _, centres, boxes in _markers(ax)
            for (cx, cy), (x0, y0, x1, y1) in zip(centres, boxes)]


def _is_span(artist, ax):
    """Is the artist a shaded span (`shade`, `axvspan`, `axhspan`)? Spans
    are drawn in the axis-blended transforms, data along one axis and the
    full panel along the other."""
    tr = mpl.artist.Artist.get_transform(artist)
    return (tr is ax.get_xaxis_transform(which='grid')
            or tr is ax.get_yaxis_transform(which='grid'))


def _shapes(ax):
    """Filled shapes on `ax`, each as (name, whether the axes clip it,
    outline in axes fraction): patches (bars, arrows, rectangles, polygons)
    and polygon collections (`fill_between`, `band`).

    The outline is the drawn path, so a diagonal arrow occupies its shaft
    and head and not the empty corners of its bounding box. Shaded spans
    are furniture a label may sit across, as rules are, and are left out;
    so is a collection repeating one shape at many offsets (`hexbin`), a
    grid of cells rather than a shape.
    """
    inv = ax.transAxes.inverted()
    out = []
    for pa in ax.patches:
        if pa.get_visible() and not _is_span(pa, ax):
            path = pa.get_transform().transform_path(pa.get_path())
            out.append((_name(pa) if _name(pa) != 'unlabelled'
                        else type(pa).__name__, pa.get_clip_on(),
                        inv.transform_path(path)))
    for coll in ax.collections:
        if (not coll.get_visible() or len(coll.get_offsets()) > 1
                or not isinstance(coll, (mpl.collections.PolyCollection,
                                         mpl.collections.PatchCollection))):
            continue
        name = (_name(coll) if _name(coll) != 'unlabelled'
                else type(coll).__name__)
        tr = coll.get_transform()
        out += [(name, coll.get_clip_on(),
                 inv.transform_path(tr.transform_path(p)))
                for p in coll.get_paths()]
    return out


def _meets(box, shape):
    """Does a box (axes fraction) cross a shape's edge or cover the whole
    shape? A box wholly inside a shape is text written on it, which is
    allowed. Where the axes clip a shape, only the part of the box inside
    the panel can meet it."""
    _, clipped, outline = shape
    if clipped:
        box = Bbox.intersection(box, Bbox.unit())
        if box is None:
            return False
    return outline.intersects_bbox(box, filled=False)


def _holds(shape, point, tol=0.004):
    """Does a shape hold `point` (axes fraction), inside it or on its edge?
    A label anchored there belongs to the shape: a value written on its
    bar, a direct label at its line's end inside that line's band."""
    return shape[2].intersects_bbox(
        Bbox.from_bounds(point[0] - tol, point[1] - tol, 2 * tol, 2 * tol),
        filled=True)


def _crosses(rect, curve):
    """Does a sampled path enter `rect`? Both in axes fraction."""
    return len(curve) > 0 and bool(
        ((curve[:, 0] >= rect.x0) & (curve[:, 0] <= rect.x1)
         & (curve[:, 1] >= rect.y0) & (curve[:, 1] <= rect.y1)).any())


def _hits(box, boxes, curves, pad=0.006, shapes=()):
    """Does `box` (axes fraction) touch another label or a marker, cross a
    data line, or cross the edge of a filled shape?"""
    r = _pad_box(box, pad)
    return (any(r.overlaps(b) for b in boxes)
            or any(_crosses(r, c) for c in curves)
            or any(_meets(r, s) for s in shapes))


def _box(artist, renderer, inv):
    """The extent of the drawn text, in axes fraction.

    `Annotation.get_window_extent` unions in the leader arrow, so a label that
    has been moved off its line would measure as tall as its own leader and
    then dodge things it does not touch. Only the text occupies space a reader
    can collide with, so only the text is measured.
    """
    if isinstance(artist, mpl.text.Annotation):
        artist.update_positions(renderer)
    return mpl.text.Text.get_window_extent(artist, renderer).transformed(inv)


def _wrapped(renderer, ann, text, cap):
    """`text` broken at spaces into the fewest lines (up to three) whose
    widest line fits within `cap` pixels, cut as evenly as those lines can
    be; `text` itself when it fits, has one word, or was broken by hand.

    Among the ways to cut a given number of lines, the one whose widest line
    is narrowest wins, ties going to the most even. A word wider than `cap`
    cannot be brought under it: the label is then cut as narrow as three
    lines allow and left to the author, who can shorten it.
    """
    words = text.split(' ')
    if '\n' in text or '$' in text or len(words) == 1:
        return text
    fp = ann.get_fontproperties()

    def width(s):
        return renderer.get_text_width_height_descent(s, fp, ismath=False)[0]

    if width(text) <= cap:
        return text
    best = None
    for n in range(2, min(3, len(words)) + 1):
        for cuts in combinations(range(1, len(words)), n - 1):
            lines = [' '.join(words[a:b])
                     for a, b in pairwise((0, *cuts, len(words)))]
            widths = [width(s) for s in lines]
            key = (max(widths), max(widths) - min(widths))
            if best is None or key < best[0]:
                best = (key, lines)
        if best[0][0] <= cap:
            break
    return '\n'.join(best[1])


def _place(renderer, ax, anns, lines, ends, fixed, gap, wrap):
    """Position every direct label on `ax`; return whether anything moved.

    Two things have to be true of a finished label: it does not sit on another
    label, and it does not sit on a plotted line. Both are properties of the
    drawn figure, not of the data, so this runs at draw time (when the axes box
    has its final size, after the layout engine has run) and recomputes from
    the anchors each pass, which makes it idempotent and safe to re-run on any
    later draw.
    """
    # Wrapping first, from each label's original text, because it changes
    # the boxes everything below is measured from; it depends on the figure
    # width, so the paper and slide renders each get their own answer.
    rewrapped = False
    if wrap is not None:
        cap = wrap * ax.figure.bbox.width
        for ann in anns:
            text = _wrapped(renderer, ann, ann._fs_text, cap)
            if text != ann.get_text():
                ann.set_text(text)
                rewrapped = True
    inv = ax.transAxes.inverted()
    boxes = [_box(a, renderer, inv) for a in anns]
    heights = [b.height for b in boxes]

    anchors = _to_axes(ax, ends)
    # Where each box sits relative to its text position, so a candidate
    # position can be tested by translating the box already measured.
    pos = _to_axes(ax, [a.get_position() for a in anns])
    off = [(b.x0 - p[0], b.y0 - p[1]) for b, p in zip(boxes, pos)]

    want = [float(a[1]) for a in anchors]
    xf = [float(a[0]) for a in anchors]

    # A series finishing within a tenth of the panel width of the right edge
    # belongs to the margin group and is spread against the rest of it; one
    # stopping earlier is labelled where it actually ends, since dragging it
    # to the margin would strand it in the middle of the data.
    xmax = max(xf)
    at_edge = [i for i, v in enumerate(xf) if v >= xmax - 0.1]

    # The separation that actually clears is the height of the labels
    # themselves, which is measurable; `gap` only raises that floor. Deriving
    # it beats hand-tuning per figure, and is the only way two-line labels get
    # the room they need without starving single-line ones.
    tall = max(heights[i] for i in at_edge)
    inset = tall / 2 + 0.012
    # The separation must cover what the ladder's floor below demands (a
    # label height plus the collision pad), or the spread's own rungs trip
    # the floor and every label bumps a full dodge step, double-spacing the
    # ladder: 1.15*tall covers it at text sizes near the default,
    # tall + 0.007 keeps it covered at small ones.
    spread = _spread([want[i] for i in at_edge],
                     max(gap or 0.0, tall * 1.15, tall + 0.007),
                     lo=inset, hi=1 - inset)
    for i, f in zip(at_edge, spread):
        want[i] = f

    curves = [_curve(ax, ln) for ln in _data_lines(ax)] + _collection_curves(ax)
    blocked = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
               + _marker_boxes(ax))

    def rect(i, y):
        return Bbox.from_bounds(pos[i][0] + off[i][0], y + off[i][1],
                                boxes[i].width, heights[i])

    shapes = _shapes(ax)

    def obstacles(i):
        # The label's own line runs right up to the anchor it is placed
        # against, so that stretch is not something to dodge.
        a = anchors[i]
        return [c[np.hypot(c[:, 0] - a[0], c[:, 1] - a[1]) > 0.025]
                for c in curves]

    def walls(i):
        # Nor is a shape holding the line's end, such as the line's band.
        return [s for s in shapes if not _holds(s, anchors[i])]

    final = list(want)
    # Margin labels first: they are already spread, so they settle at their
    # own height and become obstacles for the free-floating ones. They go
    # bottom-to-top, each barred from dipping below the top of the one just
    # placed, because the ladder's vertical order is the pairing with the
    # line ends: a free dodge could clear an obstacle by leaping over a
    # neighbour, leaving every label readable but the ladder ordered unlike
    # the lines. When nothing above the floor is clear, the label settles at
    # the floor and any remaining overlap is left for `collisions` to
    # report, which fails loudly where a swapped ladder would not.
    floor = -np.inf
    for i in sorted(at_edge, key=lambda i: want[i]):
        step = max(heights[i] * 1.05, 0.028)
        offsets = [0.0] + [s * k * step for k in range(1, 13) for s in (1, -1)]
        near, own = obstacles(i), walls(i)
        lo = max(floor, 0.005)
        final[i] = max(want[i], lo + heights[i] / 2)
        for dy in offsets:
            y = want[i] + dy
            if y - heights[i] / 2 < lo or y + heights[i] / 2 > 0.995:
                continue
            if not _hits(rect(i, y), blocked, near, shapes=own):
                final[i] = y
                break
        blocked.append(rect(i, final[i]))
        floor = final[i] + heights[i] / 2 + 0.006
    for i in range(len(anns)):
        if i in at_edge:
            continue
        step = max(heights[i] * 1.05, 0.028)
        offsets = [0.0] + [s * k * step for k in range(1, 13) for s in (1, -1)]
        near, own = obstacles(i), walls(i)
        for dy in offsets:
            y = want[i] + dy
            if y - heights[i] / 2 < 0.005 or y + heights[i] / 2 > 0.995:
                continue
            if not _hits(rect(i, y), blocked, near, shapes=own):
                final[i] = y
                break
        blocked.append(rect(i, final[i]))

    moved = False
    for i, ann in enumerate(anns):
        if abs(final[i] - pos[i][1]) > 1e-3:
            moved = True
        ann.set_position((ann.get_position()[0],
                          float(_to_data(ax, [(pos[i][0], final[i])])[0][1])))
        # A leader earns its ink only once the label sits clearly away from
        # its line end, beyond 1.5 of its own height: closer than that, the
        # word's colour and the ladder's order already carry the pairing.
        # The old fixed 2%-of-panel threshold was well under a label height,
        # sprouting stubs on labels still level with their lines.
        show = abs(final[i] - anchors[i][1]) > 1.5 * heights[i]
        if show != ann.arrow_patch.get_visible():
            moved = True
        ann.arrow_patch.set_visible(show)
    return moved or rewrapped


def _on_draw(event):
    """Re-run every deferred placement on this figure.

    Placement depends on the size of the axes box, which is not settled until
    the figure is drawn (`tight_layout` runs after the labels are added). Each
    job recomputes from its anchors, so running again is free when nothing has
    changed and self-correcting when something has.
    """
    fig = event.canvas.figure
    depth = getattr(fig, '_fs_depth', 0)
    jobs = getattr(fig, '_fs_jobs', None)
    if not jobs or depth >= 3:
        return
    fig._fs_depth = depth + 1
    try:
        # The list is deliberate: `any` over a generator would stop at the
        # first job that moved something, leaving the rest unplaced.
        moved = [job(event.renderer) for job in jobs]
        if any(moved):
            fig.canvas.draw()
    finally:
        fig._fs_depth = depth


def _defer(ax, job):
    """Run `job(renderer)` on every draw, and once now if we can."""
    fig = ax.figure
    if not hasattr(fig, '_fs_jobs'):
        fig._fs_jobs = []
        fig.canvas.mpl_connect('draw_event', _on_draw)
    fig._fs_jobs.append(job)
    # Place immediately as well, so positions are readable (and testable)
    # straight away rather than only once the figure has been drawn.
    r = _renderer(fig)
    if r is not None:
        job(r)


def _renderer(fig):
    try:
        return fig.canvas.get_renderer()
    except AttributeError:
        return None


# How far past the last point the panel ends, as a fraction of the data's
# x-span: enough to hold an end marker or a round line cap, which the panel
# edge would otherwise cut through the middle. A label starts `_LABEL_GAP` of
# the panel width past its line end, which puts a margin label just beyond
# the panel edge and an early-ending one just past where its line stops.
_END_PAD = 0.012
_LABEL_GAP = 0.018


def _may_move(ax, axis):
    """May a helper move the limits of `axis` ('x' or 'y')?

    Only while matplotlib still autoscales it, or while the limits there are
    the ones a helper last set, which a later draw re-solves. A limit the
    code set by hand is the figure's decision, a common scale across
    figures for one, and no helper moves it.
    """
    auto = ax.get_autoscalex_on() if axis == 'x' else ax.get_autoscaley_on()
    now = ax.get_xlim() if axis == 'x' else ax.get_ylim()
    return auto or tuple(now) == getattr(ax, f'_fs_{axis}lim', None)


def _set_lim(ax, axis, lo, hi):
    """Set the limits of `axis` as a helper, recording them on every axes
    sharing it, so `_may_move` can tell them from limits set by hand."""
    (ax.set_xlim if axis == 'x' else ax.set_ylim)(lo, hi)
    shared = (ax.get_shared_x_axes() if axis == 'x'
              else ax.get_shared_y_axes()).get_siblings(ax)
    for a in shared:
        setattr(a, f'_fs_{axis}lim',
                tuple(a.get_xlim() if axis == 'x' else a.get_ylim()))


def _end_panel_at(ax, xend, span):
    """End the panel a hair past `xend`, unless the x-limit was set by hand.

    Matplotlib's autoscale pads the data by 5% on each side, and a label hung
    past the last point would straddle that strip: gridlines under its first
    letters, the rest in the margin. So while the x-axis is still autoscaling
    the right limit moves to the data end plus `_END_PAD` of `span`, and the
    grid stops with the data. A limit set by hand is the figure's own
    decision (a horizon drawn past the data) and stays. Shared x-axes are
    treated as one: every panel ends at the latest end asked of any of them,
    so a panel labelled later cannot clip the lines of one labelled earlier.
    """
    if not _may_move(ax, 'x'):
        return
    shared = ax.get_shared_x_axes().get_siblings(ax)
    xend = max([xend, *(a._fs_xend for a in shared if hasattr(a, '_fs_xend'))])
    for a in shared:
        a._fs_xend = xend
    _set_lim(ax, 'x', ax.get_xlim()[0], xend + _END_PAD * span)


def label_lines(ax, fontsize=None, gap=None, x=None, texts=None, wrap=0.2):
    """Label each data line at its right-hand end, in the line's own colour.

    Replaces `ax.legend()`. Places one coloured label per line just past its
    final finite value, hanging in the figure margin beyond the panel: the
    panel stays the data region, the grid stops with the data, and the
    layout engine reserves the space the labels need (see `setup`). While
    the x-axis is still autoscaling the panel is ended a hair past the last
    line end, since autoscale's own padding would leave the labels
    straddling the panel edge; a right limit set by hand is kept, and if it
    runs past the data the labels sit inside the panel at their line ends.
    Shared x-axes end at the latest line end among the panels labelled.

    Labels are spread apart by their own measured height and moved clear of
    anything they would otherwise sit on: another label, a reference-line or
    shaded-band annotation, or a plotted line. The margin labels always read
    top-to-bottom in the order the lines end, so the ladder itself carries
    the pairing; a label moved clearly off its own line (beyond one and a
    half times its height) gets a hairline leader back to it. A series whose
    end the axes limits clip off is an error: its anchor sits outside the
    panel, and the figure's limits are what need fixing.

    A label wider than `wrap` of the figure width would take that width from
    the panel, so it is broken at spaces into the fewest lines (up to three)
    that fit, cut as evenly as possible. The decision is made at every draw,
    so the paper and slide renders each get their own answer. A label broken
    by hand (with a newline in it) is kept as written; `wrap=None` turns
    this off.

    `gap` sets a minimum separation (axes fraction) over the measured one, for
    the occasional figure that wants the labels further apart than they
    strictly need to be. `texts` overrides the labels; `x` forces a common
    label position.
    """
    fontsize = _text_size() if fontsize is None else fontsize
    lines = _plotted_lines(ax)
    if not lines:
        raise ValueError('label_lines: no labelled lines on this axes')
    if texts is not None and len(texts) != len(lines):
        raise ValueError(f'label_lines: {len(texts)} texts for '
                         f'{len(lines)} lines')

    ends, starts = [], []
    for ln in lines:
        # where the line is plotted: positions for category names, the
        # axis's own units for dates
        xd = np.asarray(ln.get_xdata(orig=False), float)
        yd = np.asarray(ln.get_ydata(orig=False), float)
        ok = np.isfinite(xd) & np.isfinite(yd)
        if not ok.any():
            raise ValueError(f'label_lines: series {ln.get_label()!r} is empty')
        j = np.flatnonzero(ok)[-1]
        ends.append((xd[j], yd[j]))
        starts.append(float(xd[ok].min()))

    xend = max(xe for xe, _ in ends)
    _end_panel_at(ax, xend, xend - min(starts))
    x0, x1 = ax.get_xlim()

    # A line whose end the limits clip off cannot be labelled at its end:
    # the anchor sits outside the panel and the label would name a line that
    # visibly stops somewhere else. That is a limits problem in the calling
    # figure, raised as one rather than papered over.
    xlo, xhi = sorted((x0, x1))
    ylo, yhi = sorted(ax.get_ylim())
    for ln, (xe, ye) in zip(lines, ends):
        if not (xlo <= xe <= xhi and ylo <= ye <= yhi):
            raise ValueError(
                f'label_lines: series {ln.get_label()!r} ends at '
                f'({xe:g}, {ye:g}), outside the axes limits; widen the '
                'limits or drop the series')

    # Anything already annotated on this axes is furniture the labels have to
    # avoid, so capture it before adding any of our own. The y-range is left
    # to autoscale: placement re-solves from the data anchors at every draw,
    # so a rule or a sibling panel's series added later just widens it.
    fixed = list(ax.texts)

    anns = []
    for i, (ln, (xe, ye)) in enumerate(zip(lines, ends)):
        # relpos attaches the stroke to the label's near edge. The default
        # aims at the centre of the text box, so a leader to a label in the
        # margin ladder tunnelled under whatever word sat between it and its
        # line end; edge-attached, it stays in the gutter beside the column.
        leader = {'arrowstyle': '-', 'color': ln.get_color(), 'lw': 0.6,
                  'alpha': 0.55, 'shrinkA': 0, 'shrinkB': 3,
                  'relpos': (0, 0.5), 'connectionstyle': 'arc3,rad=0'}
        ann = ax.annotate(
            texts[i] if texts is not None else ln.get_label(),
            xy=(xe, ye),
            xytext=((x if x is not None else xe) + _LABEL_GAP * (x1 - x0), ye),
            color=ln.get_color(), fontsize=fontsize,
            va='center', ha='left', clip_on=False, annotation_clip=False,
            arrowprops=leader,
        )
        ann.arrow_patch.set_visible(False)
        # The text as given: wrapping is re-decided from it at every draw,
        # so a label wrapped at one size can go back to one line at another.
        ann._fs_text = ann.get_text()
        anns.append(ann)

    _defer(ax, partial(_place, ax=ax, anns=anns, lines=lines, ends=ends,
                       fixed=fixed, gap=gap, wrap=wrap))


# Where a point label may sit, in order of preference: beside the point
# first, because a label level with its point is the easiest to pair up.
_SPOTS = [('left', 'center', 1.0, 0.0), ('right', 'center', -1.0, 0.0),
          ('left', 'bottom', 0.7, 0.7), ('left', 'top', 0.7, -0.7),
          ('right', 'bottom', -0.7, 0.7), ('right', 'top', -0.7, -0.7),
          ('center', 'bottom', 0.0, 1.0), ('center', 'top', 0.0, -1.0)]


def _place_points(renderer, ax, anns, pts, cloud, fixed, offset):
    """Find each point label a spot clear of the other labels and points."""
    inv = ax.transAxes.inverted()
    ap = ax.get_window_extent()
    # Offsets are given in points so they do not change with the data; the
    # geometry is done in axes fractions, so convert once.
    unit = ax.figure.dpi / 72
    ux, uy = unit / ap.width, unit / ap.height
    sizes = [_box(a, renderer, inv).size for a in anns]
    at = _to_axes(ax, pts)

    # A label must not cover a point either, its own or an unlabelled one,
    # nor a scatter's marker at its drawn size.
    marks = [Bbox.from_bounds(p[0] - 2.5 * ux, p[1] - 2.5 * uy,
                              5 * ux, 5 * uy)
             for p in _to_axes(ax, list(pts) + list(cloud))]
    marks += _marker_boxes(ax, 2.5)
    blocked = [_box(t, renderer, inv) for t in fixed if t.get_visible()]
    shapes = _shapes(ax)

    # Greedy placement gives whoever goes first the closest spot, so let the
    # points in a cluster choose before the isolated ones, which still have
    # somewhere to go afterwards.
    near = [sum(abs(p[0] - q[0]) < 0.12 and abs(p[1] - q[1]) < 0.12
                for q in at) for p in at]
    moved = False
    for i in sorted(range(len(anns)), key=lambda i: -near[i]):
        ann = anns[i]
        w, h = sizes[i]
        best = None
        # a shape holding the point (the bar or band it belongs to) is its own
        own = [s for s in shapes if not _holds(s, at[i])]
        for k in (1.0, 2.0, 3.4, 5.0, 7.0, 9.5, 12.0, 15.0):
            for ha, va, sx, sy in _SPOTS:
                dx, dy = sx * k * offset * ux, sy * k * offset * uy
                x0 = at[i][0] + dx - (w if ha == 'right' else
                                      w / 2 if ha == 'center' else 0)
                y0 = at[i][1] + dy - (h if va == 'top' else
                                      h / 2 if va == 'center' else 0)
                box = Bbox.from_bounds(x0, y0, w, h)
                if box.x0 < 0.002 or box.x1 > 0.998 or \
                        box.y0 < 0.002 or box.y1 > 0.998:
                    continue
                if not _hits(box, blocked + marks, [], pad=0.005, shapes=own):
                    best = (ha, va, sx * k * offset, sy * k * offset, k)
                    break
            if best:
                break
        if best is None:
            best = (*_SPOTS[0][:2], offset, 0.0, 1.0)
        ha, va, dx, dy, k = best
        if (ann.get_ha(), ann.get_va()) != (ha, va) or \
                tuple(np.round(ann.xyann, 3)) != (round(dx, 3), round(dy, 3)):
            moved = True
        ann.set_ha(ha)
        ann.set_va(va)
        ann.xyann = (dx, dy)
        # Once a label has been pushed away from its point, the pairing needs
        # saying; next to the point it is already obvious.
        ann.arrow_patch.set_visible(k > 1.0)
        blocked.append(Bbox.from_bounds(
            at[i][0] + dx * ux - (w if ha == 'right' else
                                  w / 2 if ha == 'center' else 0),
            at[i][1] + dy * uy - (h if va == 'top' else
                                  h / 2 if va == 'center' else 0), w, h))
    return moved


def label_points(ax, x: ArrayLike, y: ArrayLike, labels: ArrayLike,
                 fontsize=None, color='black', offset=5, avoid=()):
    """Label scattered points, each moved clear of the others.

    These labels default two points below the running text size, the one
    deliberate exception to the two-size scheme: many names have to fit
    inside the panel, and they annotate marks rather than carry them.

    A fixed offset per label works only until two points come close, and in a
    cluster the names overprint one another. This tries spots around each
    point at growing distance and takes the first clear of the other labels
    and of the points themselves, adding a hairline leader once a label has
    been pushed far enough that which point it names stops being obvious.

    `color` is one colour for every label or one per label. `avoid` takes the
    remaining (x, y) of a scatter when only some of its points are named, so
    a label does not land on an unlabelled one.
    """
    fontsize = _text_size() - 2 if fontsize is None else fontsize
    x, y, labels = list(x), list(y), list(labels)
    if not (len(x) == len(y) == len(labels)):
        raise ValueError(f'label_points: {len(x)} x, {len(y)} y, '
                         f'{len(labels)} labels')
    colors = [color] * len(labels) if isinstance(color, str) else list(color)
    if len(colors) != len(labels):
        raise ValueError(f'label_points: {len(colors)} colours for '
                         f'{len(labels)} labels')

    fixed = list(ax.texts)
    anns = []
    for xi, yi, text, c in zip(x, y, labels, colors):
        ann = ax.annotate(
            text, xy=(xi, yi), xytext=(offset, 0), textcoords='offset points',
            fontsize=fontsize, color=c, ha='left', va='center',
            annotation_clip=False,
            arrowprops={'arrowstyle': '-', 'color': GREY['rule'], 'lw': 0.5,
                        'alpha': 0.6, 'shrinkA': 0, 'shrinkB': 2},
        )
        ann.arrow_patch.set_visible(False)
        anns.append(ann)
    _defer(ax, partial(_place_points, ax=ax, anns=anns, pts=list(zip(x, y)),
                       cloud=list(avoid), fixed=fixed, offset=offset))
    return anns


_CORNERS = ['upper left', 'upper right', 'lower right', 'lower left']


def _corner(loc, w, h, lineheight, n):
    """Anchor and step for a key block of size (w, h) in the named corner."""
    ha, x0 = ('left', 0.03) if loc.endswith('left') else ('right', 0.97)
    if loc.startswith('upper'):
        va, y0 = 'top', 0.95
    else:
        va, y0 = 'bottom', 0.05 + lineheight * (n - 1)
    return ha, va, x0, y0, -lineheight


def _place_key(renderer, ax, anns, entries, lineheight, order, fixed):
    """Put a key block in the first corner the data leaves free."""
    inv = ax.transAxes.inverted()
    boxes = [_box(a, renderer, inv) for a in anns]
    if lineheight is None:
        # The step that clears is the entries' own measured height. A fixed
        # fraction of the panel overlaps as soon as the panel is small or
        # the type is big; measuring tracks both. The floor is what
        # `collisions` demands (its pad, with clearance): in a tall panel
        # the entries are small in axes fraction, and 15% of their height
        # alone leaves less than the pad between them.
        tall = max(b.height for b in boxes)
        lineheight = max(1.15 * tall, tall + 0.007)
    w = max(b.width for b in boxes)
    h = lineheight * (len(anns) - 1) + max(b.height for b in boxes)
    curves = ([_curve(ax, ln) for ln in _data_lines(ax)]
              + _collection_curves(ax) + _rule_curves(ax))
    others = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
              + _marker_boxes(ax))
    shapes = _shapes(ax)

    for loc in order:
        ha, va, x0, y0, step = _corner(loc, w, h, lineheight, len(anns))
        left = x0 if ha == 'left' else x0 - w
        top = y0 if va == 'top' else y0 + max(b.height for b in boxes)
        if not _hits(Bbox.from_bounds(left, top - h, w, h), others, curves,
                     shapes=shapes):
            break
    else:
        loc = order[0]
        ha, va, x0, y0, step = _corner(loc, w, h, lineheight, len(anns))

    moved = False
    for i, ann in enumerate(anns):
        want = (x0, y0 + i * step)
        if ann.xy != want or ann.get_ha() != ha or ann.get_va() != va:
            moved = True
        ann.xy = want
        ann.set_ha(ha)
        ann.set_va(va)
    return moved


def key(ax, entries, loc='auto', fontsize=None, lineheight=None):
    """Stack coloured labels in a corner, with no box and no swatches.

    The fallback where labels cannot sit at the line ends: in a grid of small
    multiples (name the series once, in the first panel) or where series
    finish on top of one another. Still better than a legend, because the
    colour of the word is the key, so there is nothing to look up.

    `entries` is a sequence of (text, colour) pairs. `loc` names a corner, or
    'auto' (the default) to use the first corner clear of the data: plotted
    lines, collection strokes (dot-plot stems, dumbbell connectors,
    pointrange intervals), markers, the edges of filled shapes (bars,
    arrows, bands), and reference rules, which a short direct label may sit
    across but a key block may not. The line step is
    measured from the drawn entries, so it tracks the font and panel size;
    `lineheight` (axes fraction per entry) overrides it.

    Returns the annotations it placed. That makes it a trap as the last
    statement of a captioned figure cell, where the displayed list of Text
    objects becomes a second output and Quarto renumbers the figure as
    subfigures (a) and (b); end the cell with `plt.show()`. README.md has
    the rule.
    """
    fontsize = _text_size() if fontsize is None else fontsize
    auto = loc == 'auto'
    order = _CORNERS if auto else [loc]
    fixed = list(ax.texts)
    # Provisional step for the pre-draw positions; the deferred placement
    # re-derives it from the measured text on every draw.
    ha, va, x0, y0, step = _corner(order[0], 0, 0, lineheight or 0.075,
                                   len(entries))
    anns = [ax.annotate(text, xy=(x0, y0 + i * step), xycoords='axes fraction',
                        xytext=(0, 0), textcoords='offset points',
                        color=color, fontsize=fontsize, ha=ha, va=va)
            for i, (text, color) in enumerate(entries)]
    _defer(ax, partial(_place_key, ax=ax, anns=anns, entries=entries,
                       lineheight=lineheight, order=order, fixed=fixed))
    return anns


def label_last(ax, x, y, text, color, dx=4, dy=0, fontsize=None, **kw):
    """Place one direct label at (x, y), for series drawn without a Line2D.

    Past the panel's right edge it hangs in the margin like a `label_lines`
    label, and the layout engine makes room for it; end the panel at the
    data with `ax.set_xlim` so the grid stops where the labels start.
    """
    fontsize = _text_size() if fontsize is None else fontsize
    ax.annotate(text, xy=(x, y), xytext=(dx, dy), textcoords='offset points',
                color=color, fontsize=fontsize, va='center', clip_on=False,
                **kw)


def _headroom(ax, box, curves, want):
    """Raise the top of the panel until `want` of it is free above the data.

    Only used where a label that must sit at a fixed x has no clear height
    left to occupy: the alternative is printing it over a line. A top set by
    hand stays, and the label stays on the data for `collisions` to report.
    Returns whether the limit moved.
    """
    if not _may_move(ax, 'y'):
        return False
    over = [c[(c[:, 0] >= box.x0 - 0.01) & (c[:, 0] <= box.x1 + 0.01)][:, 1]
            for c in curves]
    top = max((float(v.max()) for v in over if len(v)), default=0.0)
    if top <= 1 - want:
        return False
    y0, y1 = ax.get_ylim()
    _set_lim(ax, 'y', y0, y0 + (y1 - y0) * top / (1 - want))
    return True


# A rule's label sits this far from the rule, in points, on its usual side:
# above a horizontal rule, right of a vertical one.
_RULE_GAP = 3


def _rule_side(renderer, ax, ann, axis, raise_top):
    """Put a rule's label on the side of the rule where it fits inside the
    panel; return whether it changed side.

    The usual side is above a horizontal rule and right of a vertical one.
    When the label does not fit there, it goes to the other side if it fits
    there, unless `raise_top` says the panel's top will grow to make room
    above. When it fits on neither side it stays on the usual one, outside
    the panel, and `collisions` reports it: a limit set by hand is not moved
    to make room.
    """
    inv = ax.transAxes.inverted()
    box = _box(ann, renderer, inv)
    j = 1 if axis == 'y' else 0
    ext = box.height if axis == 'y' else box.width
    size = ax.get_window_extent().height if axis == 'y' else ax.get_window_extent().width
    gap = _RULE_GAP * ax.figure.dpi / 72 / size
    value = np.ravel(ax.convert_yunits(ann.xy[1]) if axis == 'y'
                     else ax.convert_xunits(ann.xy[0]))[0]
    point = (0.0, value) if axis == 'y' else (value, 0.0)
    at = inv.transform(ax.transData.transform(np.asarray(point, float)))[j]
    fits_usual = at + gap + ext <= 0.985
    fits_other = at - gap - ext >= 0.015
    usual = fits_usual or raise_top or not fits_other
    now = ann.xyann[j] >= 0
    if usual == now:
        return False
    off = _RULE_GAP if usual else -_RULE_GAP
    if axis == 'y':
        ann.xyann = (0, off)
        ann.set_va('bottom' if usual else 'top')
    else:
        ann.xyann = (off, 0)
        ann.set_ha('left' if usual else 'right')
    return True


def _slide(renderer, ax, ann, axis, order, fixed, grow=False, rule=False):
    """Slide a label along its own rule, or down its band, to a clear stretch.

    A reference-line or shaded-band label has to stay on the thing it names,
    so the only freedom is where along it, and for a rule's label (`rule`)
    which side of it. `order` lists candidate positions in order of
    preference and the first clear of the data and of `fixed` wins. Direct
    line labels are not in `fixed`: they are placed after this and dodge
    this label themselves, so having each avoid the other would leave the
    two of them chasing each other between draws.
    """
    # Moving the label means moving `xy`, which only carries the text with it
    # when the text is positioned as an offset from it. Anchoring it any other
    # way would leave this silently optimising a box that never moves.
    if ann.anncoords != 'offset points':
        raise ValueError('_slide: label must be placed at an offset from its '
                         f'anchor, not {ann.anncoords!r}')
    inv = ax.transAxes.inverted()
    grows = axis == 'y' and _may_move(ax, 'y')
    flipped = rule and _rule_side(renderer, ax, ann, axis, raise_top=grows)
    box = _box(ann, renderer, inv)
    curves = [_curve(ax, ln) for ln in _data_lines(ax)] + _collection_curves(ax)
    others = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
              + _marker_boxes(ax))
    shapes = _shapes(ax)
    j = 0 if axis == 'y' else 1
    here = ann.xy[j]
    for w in order:
        d = [0.0, 0.0]
        d[j] = w - here
        cand = Bbox.from_bounds(box.x0 + d[0], box.y0 + d[1], box.width, box.height)
        # a rule's label stays inside the panel along its rule
        lo, hi = (cand.x0, cand.x1) if axis == 'y' else (cand.y0, cand.y1)
        if rule and (lo < 0 or hi > 1):
            continue
        if not _hits(cand, others, curves, shapes=shapes):
            break
    else:
        # The data fills the panel wherever this label could go. Make room at
        # the top once, rather than printing the label over a line.
        if grow and not getattr(ann, '_fs_grew', False):
            ann._fs_grew = True
            if _headroom(ax, box, curves, box.height + 0.04):
                return True
        w = order[0]
    if grows and box.y1 > 0.985:
        # A rule at the top of the data (parity above a deficit) leaves its
        # label no room above the line: it would hover over the panel's edge,
        # on nothing it names. While the axis autoscales, raise the top by the
        # measured overshoot; the next draw re-measures, so a scale that is
        # not linear settles in a step or two rather than needing the
        # arithmetic to be exact. A top set by hand stays: `_rule_side` has
        # put the label below the rule instead, or left it for `collisions`.
        y0, _ = ax.get_ylim()
        _set_lim(ax, 'y', y0, float(_to_data(ax, [(0.0, box.y1 + 0.02)])[0][1]))
        return True
    if abs(w - here) < 1e-4:
        return flipped
    ann.xy = (w, ann.xy[1]) if axis == 'y' else (ann.xy[0], w)
    return True


def refline(ax, value, label=None, axis='y', where='auto', fontsize=None,
            ls=':', lw=0.9, **kw):
    """Draw a reference line and name it in place.

    An unlabelled rule at 1.0 or 0 asks the reader to work out what it means;
    `label` writes it on the line. `where` is the position along the line as an
    axes fraction, or 'auto' to slide the label to the first stretch of the
    rule that no data line and no other label is already using: a horizontal
    rule reads from the left, a vertical one from the top.

    The label sits above a horizontal rule and right of a vertical one. With
    'auto', a label above a rule at the top of the data raises the top of
    the panel to fit while the y-axis autoscales. A limit set by hand is
    never moved: a label that does not fit on its usual side goes to the
    other side of the rule if it fits there, and is otherwise left outside
    the panel for `collisions` to report.
    """
    fontsize = _text_size() if fontsize is None else fontsize
    draw = ax.axhline if axis == 'y' else ax.axvline
    draw(value, color=GREY['rule'], lw=lw, ls=ls, zorder=0.5, **kw)
    if label is None:
        return
    auto = where == 'auto'
    start = (0.02 if axis == 'y' else 0.9) if auto else where
    fixed = list(ax.texts)
    if axis == 'y':
        ann = ax.annotate(label, xy=(start, value),
                          xycoords=('axes fraction', 'data'),
                          xytext=(0, _RULE_GAP), textcoords='offset points',
                          color=GREY['text'], fontsize=fontsize,
                          ha='left', va='bottom')
    else:
        ann = ax.annotate(label, xy=(value, start),
                          xycoords=('data', 'axes fraction'),
                          xytext=(_RULE_GAP, 0), textcoords='offset points',
                          color=GREY['text'], fontsize=fontsize,
                          ha='left', va='bottom')
    ann._fs_inside = True   # names the rule in place, so belongs in the panel
    if auto:
        order = (np.arange(0.02, 0.80, 0.025) if axis == 'y'
                 else np.arange(0.92, 0.05, -0.04))
        _defer(ax, partial(_slide, ax=ax, ann=ann, axis=axis, order=order,
                           fixed=fixed, grow=axis == 'x', rule=True))
    else:
        _defer(ax, partial(_rule_side, ax=ax, ann=ann, axis=axis,
                           raise_top=False))


def shade(ax, x0, x1, label=None, where='auto', fontsize=None, **kw):
    """Shade an x-range and name it, so the band explains itself.

    The name sits at the top of the band, or at the highest point down the
    band that the data leaves free when `where` is 'auto'; where the data
    leaves none, the top of the panel rises to make room while the y-axis
    autoscales, and a top set by hand stays.
    """
    fontsize = _text_size() if fontsize is None else fontsize
    ax.axvspan(x0, x1, color=GREY['shade'], lw=0, zorder=0, **kw)
    if label is None:
        return
    auto = where == 'auto'
    fixed = list(ax.texts)
    ann = ax.annotate(label, xy=(x0 + 0.5 * (x1 - x0), 0.97 if auto else where),
                      xycoords=('data', 'axes fraction'),
                      xytext=(0, 0), textcoords='offset points',
                      color=GREY['text'], fontsize=fontsize,
                      ha='center', va='top')
    if auto:
        # A band label reads as a heading for the band, so it stays near the
        # top of it: a small slide to clear the data, and headroom rather than
        # a long slide that would leave it floating among the series.
        _defer(ax, partial(_slide, ax=ax, ann=ann, axis='x', fixed=fixed,
                           order=np.arange(0.97, 0.83, -0.035), grow=True))


def dotplot(ax, labels: ArrayLike, values: ArrayLike,
            ci: tuple[ArrayLike, ArrayLike] | None = None, color=None,
            baseline=0.0, sort=True, stem=True, markersize=5.5):
    """Cleveland dot plot: one row per category, position encodes value.

    Prefer this to a horizontal bar chart when the quantity is a difference or
    a contribution. Bars anchor the eye at zero and spend ink proportional to
    magnitude, which misleads when the scale does not start at a meaningful
    origin; a dot marks the value and nothing else. `sort` orders rows by
    value, which is what makes a ranking readable, and should stay on unless
    the category order carries meaning of its own.

    `ci` is a (lo, hi) pair of sequences, one interval per row, which turns
    the figure into a horizontal coefficient plot: the interval is drawn
    through the dot in the dot's own colour and the stems are dropped, since
    the row is already carrying a line. Pair it with `refline(ax, 0)` when
    zero is the hypothesis on trial.

    `ci` means an interval around the value the dot marks, so the dot
    belongs inside it. Two *alternative measurements* of the same quantity,
    a result at each of two prices or under each of two definitions, are not
    that: passing them as `ci` with the dot on one of them puts the mark on
    an endpoint of its own interval, and on whichever endpoint happens to be
    smaller, so the encoding flips between rows and reads as a plotting bug.
    Draw those as a dumbbell instead, one mark per measurement, named apart
    by fill rather than by position: `dotplot(..., ci=(minimum, maximum))`
    for the connector, the baseline mark on top of it, and the alternative
    as a hollow marker (`facecolor='white'`, `edgecolor=` the row's colour).
    The pair then reads the same way whichever value is larger, and
    collapses to one mark where the two measurements agree.

    Returns the axes, so a cell that ends on a bare call to this displays
    it; see the cell rule in README.md.
    """
    labels, values = list(labels), np.asarray(values, float)
    # `color` may be one colour for every row or one per row; a per-row list
    # has to be reordered alongside the values when sorting.
    color = PALETTE[0] if color is None else color
    colors = [color] * len(values) if isinstance(color, str) else list(color)
    if len(colors) != len(values):
        raise ValueError(f'dotplot: {len(colors)} colours for '
                         f'{len(values)} values')
    if ci is not None:
        lo, hi = (np.asarray(v, float) for v in ci)
        if not (len(lo) == len(hi) == len(values)):
            raise ValueError(f'dotplot: ci has {len(lo)}/{len(hi)} bounds '
                             f'for {len(values)} values')
        if np.any(lo > hi):
            raise ValueError('dotplot: ci lower bound above upper bound')
    if sort:
        o = np.argsort(values)
        labels = [labels[i] for i in o]
        colors = [colors[i] for i in o]
        values = values[o]
        if ci is not None:
            lo, hi = lo[o], hi[o]
    y = np.arange(len(values))
    if ci is not None:
        ax.hlines(y, lo, hi, color=colors, lw=1.4, alpha=0.85, zorder=2)
    elif stem:
        ax.hlines(y, baseline, values, color=GREY['grid'], lw=1.2, zorder=1)
    ax.scatter(values, y, color=colors, s=markersize ** 2, zorder=2)
    ax.set_yticks(y, labels)
    if _may_move(ax, 'y'):   # rows fill the panel, unless the caller set it
        _set_lim(ax, 'y', -0.7, len(values) - 0.3)
    grid_axis(ax, 'x')
    return ax


def _texts_within(artist):
    """The visible, non-empty texts an artist draws inside itself, at any
    depth: the pieces of a key assembled from offset boxes, a legend's
    entries."""
    for c in artist.get_children():
        if not c.get_visible():
            continue
        if isinstance(c, mpl.text.Text):
            if c.get_text().strip():
                yield c
        else:
            yield from _texts_within(c)


def _text_blocks(artists):
    """Each of `artists` that draws text inside itself (a key assembled from
    offset boxes, `AnchoredText`, a legend), with those texts. Text, axes
    and axis artists are left out: their text is measured on its own."""
    out = []
    for a in artists:
        if (isinstance(a, (mpl.text.Text, mpl.axis.Axis, mpl.axes.Axes))
                or not a.get_visible()):
            continue
        texts = list(_texts_within(a))
        if texts:
            out.append((a, texts))
    return out


def _drawn_texts(fig):
    """Every piece of text the drawn figure shows, with its panel index (-1
    for figure-level text): annotations, titles, axis titles, offset texts,
    the texts inside keys and legends, and the tick labels inside the view.
    Ticks outside the view exist as artists but are never drawn, so they
    are skipped."""
    for t in fig.texts:
        yield -1, t
    for _, texts in _text_blocks([*fig.artists, *fig.legends]):
        for t in texts:
            yield -1, t
    for n, ax in enumerate(fig.axes):
        if not ax.get_visible():
            continue
        for a in ax.get_children():
            if isinstance(a, mpl.text.Text):
                yield n, a
        for _, texts in _text_blocks(ax.get_children()):
            for t in texts:
                yield n, t
        for axis in (ax.xaxis, ax.yaxis):
            yield n, axis.label
            yield n, axis.offsetText
            lo, hi = sorted(axis.get_view_interval())
            for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
                if lo <= tick.get_loc() <= hi:
                    yield n, tick.label1
                    yield n, tick.label2


def collisions(fig, pad=0.004):
    """Report labels that overlap another label, sit on a plotted line or
    marker, or cross the edge of a filled shape, and text that runs off the
    figure.

    Direct labelling fails silently: a label landing on a line, or on another
    label, still renders and still raises nothing, so it survives until
    somebody looks at the picture. So does text past the figure's edge: the
    tight bounding box grows for most of it, which quietly breaks the
    figure's physical size, but never for an axis title's height, so a
    y-title taller than the panel is simply cut. This measures the drawn
    figure instead. Call it on any figure, not only ones built with
    `label_lines`.

    A key assembled from offset boxes (`TextArea`s packed in an
    `AnchoredOffsetbox`, `AnchoredText`) or a legend counts as one label,
    measured as the union of its texts: its pieces abut by construction, so
    only the block they make can collide with anything.

    Filled shapes are measured by their drawn outline: bars, arrows,
    rectangles and polygons, and bands (`fill_between`, `band`). A label may
    sit wholly inside a shape, which is text written on it, or clear of it,
    but not across its edge. A label pinned inside a shape or on its edge
    (an annotation's anchor or a plain text's position in data coordinates)
    belongs to that shape, and may cross that shape's edge: a value written
    at its bar's end, a direct label at its line's end inside the line's own
    band. Shaded spans (`shade`, `axvspan`) are furniture a label may cross,
    as reference rules are. Scatter markers are measured at their drawn
    size, with the same allowance near a label's own anchor as lines.

    A reference rule's label (`refline`) names the rule in place, so one
    that sits outside its panel is reported too: no helper moves a limit
    set by hand to make room for it.

    Returns a list of (panel, kind, description) tuples, empty when clean.
    `kind` is 'label/label', 'label/line', 'label/shape', 'label/marker',
    'label/outside' or 'text/edge'.
    """
    fig.canvas.draw()
    r = _renderer(fig)
    if r is None:
        raise RuntimeError('collisions: this backend exposes no renderer')

    found = []
    W, H = fig.bbox.width, fig.bbox.height
    for n, t in _drawn_texts(fig):
        if not t.get_visible() or not t.get_text().strip():
            continue
        b = mpl.text.Text.get_window_extent(t, r)
        if b.x0 < -0.5 or b.y0 < -0.5 or b.x1 > W + 0.5 or b.y1 > H + 0.5:
            found.append((n, 'text/edge',
                          f'{" ".join(t.get_text().split())!r} runs off '
                          'the figure'))
    for n, ax in enumerate(fig.axes):
        inv = ax.transAxes.inverted()
        # Names that ARE the marks (label_scatter) are data, not labels:
        # overlapping names there are overlapping observations, which the
        # figure is supposed to show.
        texts = [t for t in ax.texts if t.get_visible() and t.get_text().strip()
                 and not getattr(t, '_fs_mark', False)]
        boxes = [_box(t, r, inv) for t in texts]
        flat = [' '.join(t.get_text().split()) for t in texts]
        # A direct label is anchored to a point on its own line, so the line
        # necessarily reaches it. Only an intrusion away from that anchor is
        # a collision.
        anchors = [_to_axes(ax, [_plotted(ax, t.xy)])[0]
                   if isinstance(t, mpl.text.Annotation)
                   and t.xycoords == 'data' else None for t in texts]
        # Where a text is pinned in the data, which decides the shape it
        # belongs to: an annotation's anchor, or a plain text's position.
        homes = [a if a is not None
                 else _to_axes(ax, [_plotted(ax, t.get_position())])[0]
                 if t.get_transform() is ax.transData else None
                 for t, a in zip(texts, anchors)]
        for _, parts in _text_blocks(ax.get_children()):
            boxes.append(Bbox.union(
                [mpl.text.Text.get_window_extent(t, r) for t in parts]
            ).transformed(inv))
            flat.append(' '.join(' '.join(t.get_text().split()) for t in parts))
            anchors.append(None)
            homes.append(None)

        for i, j in combinations(range(len(boxes)), 2):
            if _pad_box(boxes[i], pad).overlaps(boxes[j]):
                found.append((n, 'label/label', f'{flat[i]!r} over {flat[j]!r}'))
        # A label naming a rule in place hovers over nothing outside the panel.
        for t, b, name in zip(texts, boxes, flat):
            if getattr(t, '_fs_inside', False) and (
                    b.x0 < -0.005 or b.y0 < -0.005 or b.x1 > 1.005 or b.y1 > 1.005):
                found.append((n, 'label/outside', f'{name!r} sits outside its panel'))

        curves = [(ln.get_label() if not ln.get_label().startswith('_')
                   else 'unlabelled', _curve(ax, ln))
                  for ln in _data_lines(ax)]
        curves += [('unlabelled stroke', c) for c in _collection_curves(ax)]
        for i, anchor in enumerate(anchors):
            for name, c in curves:
                away = c if anchor is None else c[
                    np.hypot(c[:, 0] - anchor[0], c[:, 1] - anchor[1]) > 0.025]
                if _crosses(_pad_box(boxes[i], pad), away):
                    found.append((n, 'label/line',
                                  f'{flat[i]!r} over the {name!r} line'))

        shapes, markers = _shapes(ax), _markers(ax)
        for i, (anchor, home) in enumerate(zip(anchors, homes)):
            box = _pad_box(boxes[i], pad)
            for s in shapes:
                if (home is None or not _holds(s, home)) and _meets(box, s):
                    what = f'{flat[i]!r} across the edge of the {s[0]!r} shape'
                    found.append((n, 'label/shape', what))
            inside = Bbox.intersection(box, Bbox.unit())
            for name, clipped, centres, marks in markers:
                b = inside if clipped else box
                if b is None:
                    continue
                hit = ((marks[:, 0] < b.x1) & (marks[:, 2] > b.x0)
                       & (marks[:, 1] < b.y1) & (marks[:, 3] > b.y0))
                if anchor is not None:
                    hit &= np.hypot(centres[:, 0] - anchor[0],
                                    centres[:, 1] - anchor[1]) > 0.025
                if hit.any():
                    found.append((n, 'label/marker',
                                  f'{flat[i]!r} over the {name!r} markers'))
    return found


def band(ax, x: ArrayLike, lo: ArrayLike, hi: ArrayLike, color=None,
         alpha=0.18):
    """Confidence band around the series just drawn.

    Call straight after plotting the series and the band inherits its
    colour, which is what keeps it readable as "the uncertainty of that
    line" rather than a shape of its own; pass `color` to override. Drawn
    with no edge and behind the lines, so the point estimate stays the
    figure and the band stays context.
    """
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if np.any(lo > hi):
        raise ValueError('band: lower bound above upper bound')
    if color is None:
        lines = _data_lines(ax)
        if not lines:
            raise ValueError('band: no plotted line to take a colour from; '
                             'pass color=')
        color = lines[-1].get_color()
    return ax.fill_between(x, lo, hi, color=color, alpha=alpha, lw=0,
                           zorder=1.5)


def pointrange(ax, x: ArrayLike, y: ArrayLike, lo: ArrayLike, hi: ArrayLike,
               color=None, label=None, marker='o', markersize=4.5,
               connect=False):
    """Point estimates with their intervals: coefficient profiles over a
    running variable, event studies.

    A marker at each estimate and a vertical line through its interval,
    with no caps: an error bar's caps add ink at exactly the points of the
    interval that say no more than its ends already do. For an event study,
    add `refline(ax, 0)` and, at the treatment date,
    `refline(ax, date, axis='x')`. `connect` joins the estimates, for a
    profile read as a path. Returns the marker series; give it `label` and
    `label_lines` treats it like any other series.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if not (len(x) == len(y) == len(lo) == len(hi)):
        raise ValueError(f'pointrange: {len(x)} x, {len(y)} y, '
                         f'{len(lo)}/{len(hi)} bounds')
    if np.any(lo > hi):
        raise ValueError('pointrange: lower bound above upper bound')
    kw = {'label': label} if label is not None else {}
    if color is not None:
        kw['color'] = color
    ln, = ax.plot(x, y, marker=marker, ms=markersize,
                  lw=1.1 if connect else 0, **kw)
    ax.vlines(x, lo, hi, color=ln.get_color(), lw=1.4, alpha=0.85,
              zorder=ln.get_zorder())
    return ln


def _ols_line(x, y):
    """Intercept and slope of y on x, plus the fit's R²."""
    if len(x) < 2:
        raise ValueError('OLS line needs at least two observations')
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError('OLS line: non-finite values; drop missings first')
    X = np.column_stack([np.ones(len(x)), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float((resid ** 2).sum()) / tss if tss > 0 else float('nan')
    return b, r2


def label_scatter(ax, x: ArrayLike, y: ArrayLike, labels: ArrayLike,
                  color=None, fontsize=None, fit=False, note=False):
    """Scatter in which the name is the mark.

    For a country- or industry-level scatter the reader wants to know which
    point is which more than they want a dot: printing the name at the data
    position gives every observation its identity with nothing to look up.
    The names sit exactly at their points and are never moved apart:
    position encodes value, and names overprinting each other in a cluster
    are overlapping observations, which the figure is supposed to show
    (`collisions` exempts them for the same reason). Use `label_points`
    when the marks should stay dots and the names annotate them.

    `fit` draws the OLS line through the observations as a background
    guide; `note` writes N and that fit's R² in the top-left corner.
    """
    x, y, labels = list(x), list(y), list(labels)
    if not (len(x) == len(y) == len(labels)):
        raise ValueError(f'label_scatter: {len(x)} x, {len(y)} y, '
                         f'{len(labels)} labels')
    color = PALETTE[0] if color is None else color
    colors = [color] * len(labels) if isinstance(color, str) else list(color)
    if len(colors) != len(labels):
        raise ValueError(f'label_scatter: {len(colors)} colours for '
                         f'{len(labels)} labels')
    fontsize = _text_size() - 2 if fontsize is None else fontsize
    for xi, yi, text, c in zip(x, y, labels, colors):
        t = ax.annotate(str(text), xy=(xi, yi), ha='center', va='center',
                        color=c, fontsize=fontsize)
        t._fs_mark = True
    # Text does not autoscale the axes the way plotted artists do, so feed
    # the points into the data limits by hand or the panel ignores them.
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    ax.update_datalim(np.column_stack([xa, ya]))
    ax.autoscale_view()
    if fit or note:
        b, r2 = _ols_line(xa, ya)
        if fit:
            gx = np.array([xa.min(), xa.max()])
            guide(ax, gx, b[0] + b[1] * gx)
        if note:
            ax.annotate(f'N = {len(x)}   R² = {r2:.2f}', xy=(0.02, 0.97),
                        xycoords='axes fraction', ha='left', va='top',
                        color=GREY['text'], fontsize=_text_size() - 1)


def binscatter(ax, x: ArrayLike, y: ArrayLike,
               controls: ArrayLike | None = None, bins=20, color=None,
               fit=True):
    """Binned scatter: equal-count bin means of y on x, with an OLS fit.

    `controls` residualises both x and y on the controls (with an
    intercept) before binning, the Frisch-Waugh picture, and adds each
    variable's mean back so the panel stays in the units of the raw data.
    The fit line's slope comes from the underlying observations, not the
    bin means: the dots are the visual, the microdata are the estimate.
    Missing values are an error rather than silently dropped; filter them
    before calling. Returns (bin means of x, bin means of y, slope).
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError(f'binscatter: x {x.shape} against y {y.shape}')
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError('binscatter: non-finite values; drop missings first')
    if controls is not None:
        C = np.asarray(controls, float)
        if C.ndim == 1:
            C = C[:, None]
        if len(C) != len(x) or not np.isfinite(C).all():
            raise ValueError('binscatter: controls misshaped or non-finite')
        C = np.column_stack([np.ones(len(x)), C])
        bx, *_ = np.linalg.lstsq(C, x, rcond=None)
        by, *_ = np.linalg.lstsq(C, y, rcond=None)
        x = x - C @ bx + x.mean()
        y = y - C @ by + y.mean()
    if not 1 <= bins <= len(x):
        raise ValueError(f'binscatter: {bins} bins for {len(x)} observations')
    order = np.argsort(x)
    x, y = x[order], y[order]
    edges = np.linspace(0, len(x), bins + 1).astype(int)
    xm = np.array([x[a:b].mean() for a, b in pairwise(edges)])
    ym = np.array([y[a:b].mean() for a, b in pairwise(edges)])
    ax.scatter(xm, ym, color=PALETTE[0] if color is None else color,
               s=5.5 ** 2, zorder=2)
    slope = None
    if fit:
        b, _ = _ols_line(x, y)
        gx = np.array([x[0], x[-1]])
        guide(ax, gx, b[0] + b[1] * gx)
        slope = float(b[1])
    return xm, ym, slope


def panels(n=2, aspect=0.68, width=None, **kw):
    """A row of `n` panels sized as one document-width figure.

    The grid that recurs in practice is the side-by-side pair. Sizing the
    row from the running figure width keeps a pair the same width as a
    single figure, each panel proportionally narrower, rather than letting
    matplotlib pick something unrelated to the document. `aspect` is each
    panel's height over its own width.
    """
    w = float(plt.rcParams['figure.figsize'][0]) if width is None else width
    return plt.subplots(1, n, figsize=(w, w / n * aspect), **kw)


# Physical widths, in inches, that `save` renders at, one per destination.
# Papers here are 12pt articles with a 6-6.5 inch text column, so 5.9in is
# the old 0.9\textwidth convention made physical; decks are 16:9 beamer
# frames 6.3in wide, and 4.1in is the 0.65\linewidth slot a figure usually
# gets on one. Saved at final size and included at natural size
# (\includegraphics with no width option), type lands on the page at the
# exact point size `setup` chose; scaling at inclusion time is what
# un-sizes fonts.
TARGET_WIDTHS = {'paper': 5.9, 'slides': 4.1}


def save(fig, path, targets=('paper', 'slides'), widths=None, retouch=False):
    """Write `path`.pdf for the paper and `path`_<target>.pdf for the rest.

    Re-renders the figure at each target's physical width (height keeps the
    figure's aspect ratio), letting the deferred label placement re-solve
    for that size, and refuses to write any variant whose labels collide or
    whose text runs off the figure: a bad figure on disk outlives the
    session that could have noticed it.
    The figure is restored to its on-screen size afterwards.

    Each PDF carries the figure's digest, its plotted data, in its document
    information (see `digest`), so `render-digest figure <file>` reads the
    figure back without the document that made it.

    `widths` adds or overrides target widths, e.g. {'poster': 9.0}.
    `retouch=True` also pickles the live figure beside the PDFs; `load`
    reopens it for hand-adjustment without rerunning the analysis.
    """
    from . import digest
    path = Path(path)
    if path.suffix == '.pdf':
        path = path.with_suffix('')
    if not path.parent.is_dir():
        raise FileNotFoundError(f'save: no such directory {path.parent}')
    sizes = TARGET_WIDTHS | (widths or {})
    unknown = [t for t in targets if t not in sizes]
    if unknown:
        raise ValueError(f'save: unknown targets {unknown}; '
                         f'known: {sorted(sizes)}')
    w0, h0 = fig.get_size_inches()
    written = []
    try:
        for t in targets:
            w = sizes[t]
            fig.set_size_inches(w, w * h0 / w0)
            hits = collisions(fig)  # draws first, so placement re-solves
            if hits:
                what = '\n'.join(f'  panel {p} {kind:12} {msg}'
                                 for p, kind, msg in hits)
                raise RuntimeError(f'save: labels overlap or text runs off '
                                   f'the figure at the {t} size, nothing '
                                   f'written for it:\n{what}')
            out = path.with_name(
                path.name + ('' if t == 'paper' else f'_{t}') + '.pdf')
            with digest._quiet_pdf_metadata(), digest._keep_layout(fig):
                fig.savefig(out, metadata=digest.pdf_metadata(digest.describe(fig)))
            written.append(out)
    finally:
        fig.set_size_inches(w0, h0)
        fig.canvas.draw()
    if retouch:
        pkl = path.with_suffix('.pkl')
        with open(pkl, 'wb') as f:
            pickle.dump(fig, f)
        written.append(pkl)
    return written


def load(path):
    """Reopen a figure pickled by `save(..., retouch=True)`.

    The figure comes back live: the deferred label placement travels in the
    pickle, so edits re-solve on the next draw and `save` works on it
    again.
    """
    with open(path, 'rb') as f:
        fig = pickle.load(f)
    # Pickling drops the canvas, and the draw hook that re-runs placement
    # was registered on it; reconnect, or the reopened figure's labels
    # would go stale on the first resize.
    if getattr(fig, '_fs_jobs', None):
        fig.canvas.mpl_connect('draw_event', _on_draw)
    return fig
