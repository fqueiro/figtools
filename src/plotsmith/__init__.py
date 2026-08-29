"""Shared figure style for research documents.

One call to `setup()` at the top of a document and every figure in it matches
every figure in every other project that uses this package: the style, the
direct-labelling machinery that replaces legends, the small set of plot types
the work actually uses (time series, dot plots, pointranges, binned and
labelled scatters), the per-destination export, and the measurement that
keeps direct labelling honest.

The choices follow Kieran Healy's *Data Visualization* practices:

  * One colour-blind-safe categorical palette (Tol vibrant), assigned in a
    fixed order and never cycled, so a colour means the same entity
    series-wide.
  * Direct labelling in place of legends. A legend forces a reader to hold a
    colour in memory and hop between the key and the line; a label at the end
    of the line does not. `label_lines`, `label_points` and `label_last` do
    this, and `key` is the fallback for the figure that cannot.
  * Gridlines only on the axis carrying the measurement, drawn light and
    behind the data, so they support reading values without competing.
  * No frame around the data at all: no spines, no tick marks. The grid and
    the floating tick labels carry every reference the reader needs; axis
    lines are frame ink.
  * Reference lines and shaded bands annotated in place ("parity", "fragile
    data") rather than left for the reader to infer.
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
pairing stops being obvious. That has to happen when the figure is drawn,
because it depends on how big the panel turned out to be, so it runs from a
draw callback and recomputes on every draw rather than being decided when the
label is created. `collisions` measures a drawn figure for what is left; it
is the check to run on a figure rather than squinting at it.
"""

import pickle
from functools import partial
from itertools import combinations, pairwise
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.transforms import Bbox

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
    'make_room',
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


def setup(base=BASE_SIZE):
    """Apply the figure style. Call once per document.

    `base` is the one typographic parameter. Titles and axis labels are set
    at `base`; ticks, line labels, key entries and annotations all sit half
    a point below it, resolved from the live rcParams by every helper, so
    the two sizes move together and nothing can drift apart element by
    element. Scale a whole document (a slide deck, a poster) by changing
    `base`, never by touching individual elements.
    """
    text = base - 0.5
    plt.rcParams.update({
        # Sizing. 110 dpi renders crisply in the HTML documents without
        # inflating them; savefig is higher for anything pulled into a paper.
        'figure.dpi': 110,
        'savefig.dpi': 200,
        'savefig.bbox': 'tight',

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


def guide(ax, x, y, lw=1.0, **kw):
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
    """Nudge positions apart so labels stacked at one height clear each other.

    Preserves the vertical order of the series, so a label never crosses the
    line above or below it, and keeps the whole block inside [lo, hi]: series
    that all finish at nearly the same level would otherwise be pushed off
    the panel by the re-centring.
    """
    ys = list(ys)
    if len(ys) == 1:
        return ys
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    # Fall back to an even split when the labels cannot all fit at `gap`.
    gap = min(gap, (hi - lo) / (len(ys) - 1))
    out = list(ys)
    for k, i in enumerate(order[1:], start=1):
        below = out[order[k - 1]]
        if out[i] - below < gap:
            out[i] = below + gap
    # Re-centre on the original positions, then slide the block back inside
    # the panel if that pushed either end out.
    shift = np.mean(ys) - np.mean(out)
    bottom, top = out[order[0]] + shift, out[order[-1]] + shift
    if bottom < lo:
        shift += lo - bottom
    elif top > hi:
        shift -= top - hi
    return [y + shift for y in out]


def _to_axes(ax, pts):
    """Data coordinates -> axes fraction, honouring log and inverted scales."""
    pts = np.atleast_2d(np.asarray(pts, float))
    return ax.transAxes.inverted().transform(ax.transData.transform(pts))


def _to_data(ax, pts):
    """Axes fraction -> data coordinates."""
    pts = np.atleast_2d(np.asarray(pts, float))
    return ax.transData.inverted().transform(ax.transAxes.transform(pts))


def _curve(ax, line, step=0.004):
    """A line's path in axes fraction, resampled fine enough to test against.

    Testing a label box against the plotted points alone would miss a steep
    segment that crosses the box between two observations, which is exactly
    where a line and a label are most likely to collide.
    """
    xd = np.asarray(line.get_xdata(), float)
    yd = np.asarray(line.get_ydata(), float)
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
    if len(pts) < 2:
        return pts
    out = [pts[:1]]
    for a, b in pairwise(pts):
        n = max(1, int(np.ceil(float(np.hypot(*(b - a))) / step)))
        out.append(a + np.linspace(0, 1, n + 1)[1:, None] * (b - a))
    return np.vstack(out)


def _pad_box(box, pad):
    return Bbox.from_extents(box.x0 - pad, box.y0 - pad,
                             box.x1 + pad, box.y1 + pad)


def _marker_boxes(ax, size=3.0):
    """Small boxes around plotted markers, in axes fraction.

    Scatters and dot plots draw their data as a collection rather than a
    line, so testing lines alone would let a label land on a point.
    """
    ap = ax.get_window_extent()
    unit = size * ax.figure.dpi / 72
    ux, uy = unit / ap.width, unit / ap.height
    out = []
    for coll in ax.collections:
        if not isinstance(coll, mpl.collections.PathCollection):
            continue
        off = coll.get_offsets()
        if off is None or len(off) == 0:
            continue
        for p in _to_axes(ax, np.asarray(off, float)):
            out.append(Bbox.from_bounds(p[0] - ux, p[1] - uy, 2 * ux, 2 * uy))
    return out


def _crosses(rect, curve):
    """Does a sampled path enter `rect`? Both in axes fraction."""
    return len(curve) > 0 and bool(
        ((curve[:, 0] >= rect.x0) & (curve[:, 0] <= rect.x1)
         & (curve[:, 1] >= rect.y0) & (curve[:, 1] <= rect.y1)).any())


def _hits(box, boxes, curves, pad=0.006):
    """Does `box` (axes fraction) touch another label or cross a data line?"""
    r = _pad_box(box, pad)
    return (any(r.overlaps(b) for b in boxes)
            or any(_crosses(r, c) for c in curves))


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


def _place(renderer, ax, anns, lines, ends, fixed, gap, room):
    """Position every direct label on `ax`; return whether anything moved.

    Two things have to be true of a finished label: it does not sit on another
    label, and it does not sit on a plotted line. Both are properties of the
    drawn figure, not of the data, so this runs at draw time (when the axes box
    has its final size, after `tight_layout`) and recomputes from the anchors
    each pass, which makes it idempotent and safe to re-run on any later draw.
    """
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

    # A series finishing within a tenth of the data range of the right edge
    # belongs to the margin group and is spread against the rest of it; one
    # stopping earlier is labelled where it actually ends, since dragging it
    # to the margin would strand it in the middle of the data.
    xmax = max(xf)
    at_edge = [i for i, v in enumerate(xf) if v >= xmax - 0.1 / (1 + room)]

    # The separation that actually clears is the height of the labels
    # themselves, which is measurable; `gap` only raises that floor. Deriving
    # it beats hand-tuning per figure, and is the only way two-line labels get
    # the room they need without starving single-line ones.
    tall = max(heights[i] for i in at_edge)
    inset = tall / 2 + 0.012
    spread = _spread([want[i] for i in at_edge], max(gap or 0.0, tall * 1.15),
                     lo=inset, hi=1 - inset)
    for i, f in zip(at_edge, spread):
        want[i] = f

    curves = [_curve(ax, ln) for ln in _data_lines(ax)]
    blocked = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
               + _marker_boxes(ax))

    def rect(i, y):
        return Bbox.from_bounds(pos[i][0] + off[i][0], y + off[i][1],
                                boxes[i].width, heights[i])

    def obstacles(i):
        # The label's own line runs right up to the anchor it is placed
        # against, so that stretch is not something to dodge.
        a = anchors[i]
        return [c[np.hypot(c[:, 0] - a[0], c[:, 1] - a[1]) > 0.025]
                for c in curves]

    final = list(want)
    # Margin labels first: they are already spread, so they settle at their
    # own height and become obstacles for the free-floating ones.
    for i in at_edge + [i for i in range(len(anns)) if i not in at_edge]:
        step = max(heights[i] * 1.05, 0.028)
        offsets = [0.0] + [s * k * step for k in range(1, 13) for s in (1, -1)]
        near = obstacles(i)
        for dy in offsets:
            y = want[i] + dy
            if y - heights[i] / 2 < 0.005 or y + heights[i] / 2 > 0.995:
                continue
            if not _hits(rect(i, y), blocked, near):
                final[i] = y
                break
        blocked.append(rect(i, final[i]))

    moved = False
    for i, ann in enumerate(anns):
        if abs(final[i] - pos[i][1]) > 1e-3:
            moved = True
        ann.set_position((ann.get_position()[0],
                          float(_to_data(ax, [(pos[i][0], final[i])])[0][1])))
        # A leader is clutter on a label already sitting on its line, and the
        # only thing making the pairing readable once one has been moved off.
        ann.arrow_patch.set_visible(abs(final[i] - anchors[i][1]) > 0.02)
    return moved


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


def label_lines(ax, room=0.22, fontsize=None, gap=None, x=None, texts=None):
    """Label each data line at its right-hand end, in the line's own colour.

    Replaces `ax.legend()`. Extends the x-limit by `room` (a fraction of the
    current x-range) to make space, then places one coloured label per line at
    its final finite value. Labels are spread apart by their own measured
    height and moved clear of anything they would otherwise sit on: another
    label, a reference-line or shaded-band annotation, or a plotted line. A
    label moved off its own line gets a hairline leader back to it.

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

    x0, x1 = ax.get_xlim()
    ax.set_xlim(x0, x1 + room * (x1 - x0))
    # The added margin is label space, not data space: keep ticks off it so
    # the axis does not advertise a range the data never reaches.
    _trim_ticks(ax, 'x', x0, x1)

    ends = []
    for ln in lines:
        xd, yd = np.asarray(ln.get_xdata(), float), np.asarray(ln.get_ydata(), float)
        ok = np.isfinite(xd) & np.isfinite(yd)
        if not ok.any():
            raise ValueError(f'label_lines: series {ln.get_label()!r} is empty')
        j = np.flatnonzero(ok)[-1]
        ends.append((xd[j], yd[j]))

    # Anything already annotated on this axes is furniture the labels have to
    # avoid, so capture it before adding any of our own.
    fixed = list(ax.texts)
    # Freeze the limits: placement is computed in axes fractions, so a later
    # autoscale would silently move every label off its line.
    ax.set_ylim(*ax.get_ylim())

    anns = []
    for i, (ln, (xe, ye)) in enumerate(zip(lines, ends)):
        leader = {'arrowstyle': '-', 'color': ln.get_color(), 'lw': 0.6,
                  'alpha': 0.55, 'shrinkA': 0, 'shrinkB': 3,
                  'connectionstyle': 'arc3,rad=0'}
        ann = ax.annotate(
            texts[i] if texts is not None else ln.get_label(),
            xy=(xe, ye),
            xytext=((x if x is not None else xe) + 0.012 * (x1 - x0), ye),
            color=ln.get_color(), fontsize=fontsize,
            va='center', ha='left', clip_on=False, annotation_clip=False,
            arrowprops=leader,
        )
        ann.arrow_patch.set_visible(False)
        anns.append(ann)

    _defer(ax, partial(_place, ax=ax, anns=anns, lines=lines, ends=ends,
                       fixed=fixed, gap=gap, room=room))


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

    # A label must not cover a point either, its own or an unlabelled one.
    marks = [Bbox.from_bounds(p[0] - 2.5 * ux, p[1] - 2.5 * uy,
                              5 * ux, 5 * uy)
             for p in _to_axes(ax, list(pts) + list(cloud))]
    blocked = [_box(t, renderer, inv) for t in fixed if t.get_visible()]

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
                if not _hits(box, blocked + marks, [], pad=0.005):
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


def label_points(ax, x, y, labels, fontsize=None, color='black', offset=5,
                 avoid=()):
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
        # the type is big; measuring tracks both.
        lineheight = 1.15 * max(b.height for b in boxes)
    w = max(b.width for b in boxes)
    h = lineheight * (len(anns) - 1) + max(b.height for b in boxes)
    curves = [_curve(ax, ln) for ln in _data_lines(ax)]
    others = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
              + _marker_boxes(ax))

    for loc in order:
        ha, va, x0, y0, step = _corner(loc, w, h, lineheight, len(anns))
        left = x0 if ha == 'left' else x0 - w
        top = y0 if va == 'top' else y0 + max(b.height for b in boxes)
        if not _hits(Bbox.from_bounds(left, top - h, w, h), others, curves):
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
    'auto' to use the first corner the data leaves free. The line step is
    measured from the drawn entries, so it tracks the font and panel size;
    `lineheight` (axes fraction per entry) overrides it.
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


def _trim_ticks(ax, axis, lo, hi):
    """Drop ticks outside [lo, hi], keeping any hand-set tick text.

    Re-setting ticks by position alone would discard category labels, so the
    labels are carried across with their positions.
    """
    a = ax.xaxis if axis == 'x' else ax.yaxis
    pos = list(a.get_ticklocs())
    txt = [t.get_text() for t in a.get_ticklabels()]
    keep = [i for i, t in enumerate(pos) if lo <= t <= hi]
    setter = ax.set_xticks if axis == 'x' else ax.set_yticks
    # Empty strings mean matplotlib is still formatting these itself; let it.
    if len(txt) == len(pos) and any(txt):
        setter([pos[i] for i in keep], [txt[i] for i in keep])
    else:
        setter([pos[i] for i in keep])


def make_room(ax, room=0.22, axis='x'):
    """Widen an axis limit to leave space for direct labels beyond the data.

    `label_lines` does this itself; call it explicitly when placing labels
    with `label_last`. Ticks stay inside the original data range.
    """
    get, setl = (ax.get_xlim, ax.set_xlim) if axis == 'x' else (ax.get_ylim, ax.set_ylim)
    lo, hi = get()
    setl(lo, hi + room * (hi - lo))
    _trim_ticks(ax, axis, lo, hi)


def label_last(ax, x, y, text, color, dx=4, dy=0, fontsize=None, **kw):
    """Place one direct label at (x, y), for series drawn without a Line2D."""
    fontsize = _text_size() if fontsize is None else fontsize
    ax.annotate(text, xy=(x, y), xytext=(dx, dy), textcoords='offset points',
                color=color, fontsize=fontsize, va='center', clip_on=False,
                **kw)


def _headroom(ax, box, curves, want):
    """Raise the top of the panel until `want` of it is free above the data.

    Only used where a label that must sit at a fixed x has no clear height
    left to occupy: the alternative is printing it over a line. Returns
    whether the limit moved.
    """
    over = [c[(c[:, 0] >= box.x0 - 0.01) & (c[:, 0] <= box.x1 + 0.01)][:, 1]
            for c in curves]
    top = max((float(v.max()) for v in over if len(v)), default=0.0)
    if top <= 1 - want:
        return False
    y0, y1 = ax.get_ylim()
    ax.set_ylim(y0, y0 + (y1 - y0) * top / (1 - want))
    return True


def _slide(renderer, ax, ann, axis, order, fixed, grow=False):
    """Slide a label along its own rule, or down its band, to a clear stretch.

    A reference-line or shaded-band label has to stay on the thing it names,
    so the only freedom is where along it. `order` lists candidate positions
    in order of preference and the first clear of the data and of `fixed`
    wins. Direct line labels are not in `fixed`: they are placed after this
    and dodge this label themselves, so having each avoid the other would
    leave the two of them chasing each other between draws.
    """
    # Moving the label means moving `xy`, which only carries the text with it
    # when the text is positioned as an offset from it. Anchoring it any other
    # way would leave this silently optimising a box that never moves.
    if ann.anncoords != 'offset points':
        raise ValueError('_slide: label must be placed at an offset from its '
                         f'anchor, not {ann.anncoords!r}')
    inv = ax.transAxes.inverted()
    box = _box(ann, renderer, inv)
    curves = [_curve(ax, ln) for ln in _data_lines(ax)]
    others = ([_box(t, renderer, inv) for t in fixed if t.get_visible()]
              + _marker_boxes(ax))
    j = 0 if axis == 'y' else 1
    here = ann.xy[j]
    for w in order:
        d = [0.0, 0.0]
        d[j] = w - here
        if not _hits(Bbox.from_bounds(box.x0 + d[0], box.y0 + d[1],
                                      box.width, box.height), others, curves):
            break
    else:
        # The data fills the panel wherever this label could go. Make room at
        # the top once, rather than printing the label over a line.
        if grow and not getattr(ann, '_fs_grew', False):
            ann._fs_grew = True
            if _headroom(ax, box, curves, box.height + 0.04):
                return True
        w = order[0]
    if abs(w - here) < 1e-4:
        return False
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
                          xytext=(0, 3), textcoords='offset points',
                          color=GREY['text'], fontsize=fontsize,
                          ha='left', va='bottom')
    else:
        ann = ax.annotate(label, xy=(value, start),
                          xycoords=('data', 'axes fraction'),
                          xytext=(3, 0), textcoords='offset points',
                          color=GREY['text'], fontsize=fontsize,
                          ha='left', va='bottom')
    if auto:
        order = (np.arange(0.02, 0.80, 0.025) if axis == 'y'
                 else np.arange(0.92, 0.05, -0.04))
        _defer(ax, partial(_slide, ax=ax, ann=ann, axis=axis, order=order,
                           fixed=fixed, grow=axis == 'x'))


def shade(ax, x0, x1, label=None, where='auto', fontsize=None, **kw):
    """Shade an x-range and name it, so the band explains itself.

    The name sits at the top of the band, or at the highest point down the
    band that the data leaves free when `where` is 'auto'.
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


def dotplot(ax, labels, values, ci=None, color=None, baseline=0.0, sort=True,
            stem=True, markersize=5.5):
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
    ax.set_ylim(-0.7, len(values) - 0.3)
    grid_axis(ax, 'x')
    return ax


def collisions(fig, pad=0.004):
    """Report labels that overlap another label or sit on a plotted line.

    Direct labelling fails silently: a label landing on a line, or on another
    label, still renders and still raises nothing, so it survives until
    somebody looks at the picture. This measures the drawn figure instead.
    Call it on any figure, not only ones built with `label_lines`.

    Returns a list of (panel, kind, description) tuples, empty when clean.
    `kind` is 'label/label' or 'label/line'.
    """
    fig.canvas.draw()
    r = _renderer(fig)
    if r is None:
        raise RuntimeError('collisions: this backend exposes no renderer')

    found = []
    for n, ax in enumerate(fig.axes):
        inv = ax.transAxes.inverted()
        # Names that ARE the marks (label_scatter) are data, not labels:
        # overlapping names there are overlapping observations, which the
        # figure is supposed to show.
        texts = [t for t in ax.texts if t.get_visible() and t.get_text().strip()
                 and not getattr(t, '_fs_mark', False)]
        boxes = [_box(t, r, inv) for t in texts]
        flat = [' '.join(t.get_text().split()) for t in texts]

        for i, j in combinations(range(len(texts)), 2):
            if _pad_box(boxes[i], pad).overlaps(boxes[j]):
                found.append((n, 'label/label', f'{flat[i]!r} over {flat[j]!r}'))

        curves = [(ln.get_label() if not ln.get_label().startswith('_')
                   else 'unlabelled', _curve(ax, ln))
                  for ln in _data_lines(ax)]
        for i, t in enumerate(texts):
            # A direct label is anchored to a point on its own line, so the
            # line necessarily reaches it. Only an intrusion away from that
            # anchor is a collision.
            anchor = (_to_axes(ax, [t.xy])[0]
                      if isinstance(t, mpl.text.Annotation)
                      and t.xycoords == 'data' else None)
            for name, c in curves:
                away = c if anchor is None else c[
                    np.hypot(c[:, 0] - anchor[0], c[:, 1] - anchor[1]) > 0.025]
                if _crosses(_pad_box(boxes[i], pad), away):
                    found.append((n, 'label/line',
                                  f'{flat[i]!r} over the {name!r} line'))
    return found


def band(ax, x, lo, hi, color=None, alpha=0.18):
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


def pointrange(ax, x, y, lo, hi, color=None, label=None, marker='o',
               markersize=4.5, connect=False):
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


def label_scatter(ax, x, y, labels, color=None, fontsize=None, fit=False,
                  note=False):
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


def binscatter(ax, x, y, controls=None, bins=20, color=None, fit=True):
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
    for that size, and refuses to write any variant whose labels collide: a
    bad figure on disk outlives the session that could have noticed it.
    The figure is restored to its on-screen size afterwards.

    `widths` adds or overrides target widths, e.g. {'poster': 9.0}.
    `retouch=True` also pickles the live figure beside the PDFs; `load`
    reopens it for hand-adjustment without rerunning the analysis.
    """
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
                raise RuntimeError(f'save: label overlaps at the {t} size, '
                                   f'nothing written for it:\n{what}')
            out = path.with_name(
                path.name + ('' if t == 'paper' else f'_{t}') + '.pdf')
            fig.savefig(out)
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
