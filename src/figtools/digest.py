"""Machine-readable digests of figures, and text renderings of a document's outputs.

A rendered document shows its findings in figures and tables that a reader
sees and a program cannot: the source holds the code that made them, the
page holds the pixels. This module gives every figure a *digest*, the
plotted data as JSON, and after a render collects the digests, the tables
and the printed output of a document into one markdown file a program can
read.

Mechanism. `install()` (run by `setup()`) wraps the formatters a notebook
kernel uses to turn a figure into its PNG or PDF, so the image carries the
digest under the key `figtools-digest`: a compressed text chunk in a PNG, an
entry of the document information dictionary in a PDF, split into pieces
under 32 kB where a reader may cap a string. Terminal IPython runs no
kernel, so there `install()` leaves the shell and its window backend alone.
`uninstall()` puts back the formatters `install()` wrapped, and
`installed()` says whether the digest is armed. `save` writes the same
entry into the PDFs it exports for a paper. The image is otherwise
unchanged and nothing else appears in the cell: a second output in a
captioned figure cell would turn the pair into subfigures.

After a render, `post_render()` (Quarto's `post-render` hook) finds the
source of each page or PDF the render wrote, at the same path under the
project folder as the output has under the output folder (`output-dir`, a
website's `_site`, a book's `_book`; without one, beside the output). An
output whose source runs no figtools code (a plain Markdown page) is
skipped with a line saying so, and so is a PDF no single document made (a
book's). It reads each figure's image back out of the page, embedded in it
or linked beside it (Quarto's default writes `<document>_files/figure-html/`
next to the page), writes the digests as markdown and CSV beside the
images, converts the page's tables and printed output to text, and stamps
the lot with a hash of the source, so a reader can tell whether the
document changed since. Everything lands under
`<cache>/<project folder name>/<document path>/`, outside the repository,
where the cache is `~/.local/share/render-digests` or the absolute folder
the environment variable `FIGTOOLS_DIGEST_DIR` names, read at each call.
Each folder's manifest records the project's absolute path, and a render
refuses to overwrite the digest of another project with the same folder
name while that project's folder still holds its `_quarto.yml`; the
variable gives one of them a cache of its own. The digest of a project
that moved or was deleted is stale, and the next render replaces it. A
document rendered to PDF yields its figures' digests, read from the
document itself since the LaTeX engine copies each figure file's entry
into it, under `pdf/` in the same folder.

What a digest holds, per panel: the axis titles, scales and kinds (number,
date, category); each plotted series with its label, colour and values
(lines and marker series, bars, scatters, bands, intervals, error bars with
or without their points); reference rules and shaded spans with their
in-place labels; points named in data coordinates; notes, which are text
written in axes coordinates, the lines of text packed in offset boxes and
the legend entries that name no series, each with its colour; small image
grids in full and larger ones by shape and range; and what was not
digested. A key entry names the unlabelled series drawn in its colour: an
entry of `key`, a line of a key packed from offset boxes, a legend entry.
Other notes, such as a panel letter, name no series. Series longer than
`SERIES_CAP` points are thinned evenly and say so. Values keep six
significant digits.

    render-digest post-render        # what `_quarto.yml` runs after a render
    render-digest where <document>   # the folder a document's digest lands in
    render-digest check <document>   # exit 1 when the source changed since the render, 2 with no digest
    render-digest extract <page.html> [--source <document>] [--out <folder>]
    render-digest figure <figure.png|.pdf> ...   # print the digest an image carries
"""

import argparse
import base64
import contextlib
import csv
import hashlib
import json
import math
import os
import re
import struct
import sys
import warnings
import zlib
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import matplotlib as mpl
import matplotlib.category
import matplotlib.dates as mdates
import matplotlib.offsetbox
import numpy as np

from figtools import GREY

KEY = 'figtools-digest'
VERSION = 1
CACHE_ROOT = Path.home() / '.local' / 'share' / 'render-digests'   # unless FIGTOOLS_DIGEST_DIR is set
SOURCES = ('.py', '.qmd', '.ipynb')   # the suffixes a rendered document's source may have
PROSE_SOURCES = ('.md', '.markdown', '.Rmd')   # sources of pages that run no figtools code
QUARTO_FILES = ('_quarto.yml', '_quarto.yaml')   # either marks a Quarto project's folder
SERIES_CAP = 5000   # points kept per series in a digest; longer ones are thinned evenly
TABLE_CELLS = 600   # cells a markdown table may hold before its rows are thinned
PDF_PIECE = 30000   # characters per information-dictionary string; some readers cap one at 32767 bytes


# --- reading a figure ---------------------------------------------------------

def _num(f):
    """A float as JSON: an int when whole, else six significant digits."""
    if abs(f) < 1e15 and f == round(f):
        return round(f)
    return float(f'{f:.6g}')


def _axis_kind(axis):
    """How an axis is read: ('number' | 'date' | 'category', position -> label)."""
    fmt = axis.get_major_formatter()
    conv = (axis.get_converter() if hasattr(axis, 'get_converter')
            else getattr(axis, 'converter', None))
    if 'Date' in type(conv).__name__ or 'Date' in type(fmt).__name__:
        return 'date', None
    if isinstance(fmt, matplotlib.category.StrCategoryFormatter):
        # Strings plotted directly: the formatter holds category -> position.
        units = getattr(fmt, '_units', None) or getattr(axis.get_units(), '_mapping', {})
        return 'category', {float(v): str(k) for k, v in dict(units).items()}
    # Ticks set by hand with labels (`set_yticks(y, labels)`): a fixed locator
    # with a fixed formatter, or the dict-backed function formatter matplotlib
    # builds for it. Any other function formatter is a number format.
    locator = axis.get_major_locator()
    fixed = isinstance(fmt, mpl.ticker.FixedFormatter) or (
        isinstance(fmt, mpl.ticker.FuncFormatter)
        and getattr(getattr(fmt.func, 'func', None), '__name__', '') == '_format_with_dict')
    if isinstance(locator, mpl.ticker.FixedLocator) and fixed:
        locs = list(locator.locs)
        mapping = {float(loc): str(fmt(loc, i)) for i, loc in enumerate(locs) if str(fmt(loc, i))}
        if mapping:
            return 'category', mapping
    return 'number', None


def _reader(kind, mapping):
    """A function turning one coordinate on an axis into its JSON value."""
    def read(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        if not np.isfinite(f):
            return None
        if kind == 'date':
            t = mdates.num2date(f)
            if (t.hour, t.minute, t.second, t.microsecond) == (0, 0, 0, 0):
                return t.date().isoformat()
            return t.replace(tzinfo=None).isoformat(timespec='minutes')
        if kind == 'category' and mapping:
            r = float(round(f))
            if abs(f - r) < 1e-9 and r in mapping:
                return mapping[r]
        return _num(f)
    return read


def _label(artist):
    lab = artist.get_label()
    lab = str(lab) if lab is not None else ''
    return None if not lab or lab.startswith('_') else lab


def _color(c):
    try:
        return mpl.colors.to_hex(c)
    except (ValueError, TypeError):
        return None


def _first_color(colors):
    colors = np.atleast_2d(np.asarray(colors))
    return _color(colors[0]) if len(colors) and colors.shape[-1] in (3, 4) else None


def _any_color(c):
    """A colour given as a name, a tuple or an array of them, as hex; None
    when there is none."""
    if isinstance(c, str):
        return _color(c)
    a = np.asarray(c)
    if not a.size:
        return None
    return _color(tuple(a)) if a.ndim == 1 else _first_color(a)


def _handle_color(h):
    """The colour a legend entry's handle is drawn in; None when it has none
    to read."""
    for get in ('get_color', 'get_facecolor'):
        c = _any_color(getattr(h, get)()) if hasattr(h, get) else None
        if c:
            return c
    return None


def _stacks(artist):
    """Does the artist stack anything vertically inside itself?"""
    return any(isinstance(c, mpl.offsetbox.VPacker) or _stacks(c) for c in artist.get_children())


def _packs(artist):
    """Does the artist pack text side by side or in a stack, as a key built
    from offset boxes does? A lone `AnchoredText`, such as a panel letter,
    packs nothing."""
    return any(isinstance(c, (mpl.offsetbox.HPacker, mpl.offsetbox.VPacker)) or _packs(c)
               for c in artist.get_children())


def _text_lines(artist):
    """The lines of text an artist draws inside itself, each a list of its
    pieces as (text, colour). Texts packed side by side in an `HPacker` read
    as one line, as the coloured words of a key do; any other text is a
    line of its own."""
    if isinstance(artist, mpl.text.Text):
        return [[(artist.get_text(), _color(artist.get_color()))]] if artist.get_visible() else []
    kids = [c for c in artist.get_children() if c.get_visible()]
    lines = [line for c in kids for line in _text_lines(c)]
    if isinstance(artist, mpl.offsetbox.HPacker) and not _stacks(artist):
        return [[p for line in lines for p in line]] if lines else []
    return lines


def _line_note(parts):
    """One line of boxed text as a note: its words, with its colour when it
    has one, and each coloured piece when its words differ in colour."""
    note = {'text': ' '.join(''.join(t for t, _ in parts).split())}
    pieces = [(' '.join(t.split()), c) for t, c in parts if t.strip()]
    colors = {c for _, c in pieces}
    if len(colors) == 1:
        note['color'] = colors.pop()
    elif colors:
        note['parts'] = [{'text': t, 'color': c} for t, c in pieces]
    return note


def _cap(s):
    """Thin a series longer than SERIES_CAP evenly, and say so."""
    n = len(s.get('x') or s.get('y') or s.get('at') or s.get('category')
            or s.get('segments') or [])
    if n <= SERIES_CAP:
        return
    keep = np.unique(np.linspace(0, n - 1, SERIES_CAP).round().astype(int))
    for k, v in list(s.items()):
        if isinstance(v, list) and len(v) == n:
            s[k] = [v[i] for i in keep]
    s['thinned'] = f'{n} points, {len(keep)} kept evenly'


def _panel(ax):
    """One axes as a dict: its axes, series, rules, shades, points and notes."""
    xk, xmap = _axis_kind(ax.xaxis)
    yk, ymap = _axis_kind(ax.yaxis)
    X, Y = _reader(xk, xmap), _reader(yk, ymap)
    p = {'title': next((t for t in (ax.get_title(loc) for loc in ('left', 'center', 'right')) if t), None),
         'xlabel': ax.get_xlabel() or None, 'ylabel': ax.get_ylabel() or None,
         'xkind': xk, 'ykind': yk}
    for name, scale in (('xscale', ax.get_xscale()), ('yscale', ax.get_yscale())):
        if scale != 'linear':
            p[name] = scale
    series, rules, shades, points, notes, images, missing = [], [], [], [], [], [], []
    claimed = set()

    # Containers first: their artists also sit in the generic lists below.
    for c in ax.containers:
        if isinstance(c, mpl.container.BarContainer):
            rects = [r for r in c.patches if r.get_visible()]
            claimed.update(id(r) for r in rects)
            if not rects:
                continue
            horizontal = getattr(c, 'orientation', 'vertical') == 'horizontal'
            s = {'kind': 'bars', 'label': _label(c), 'color': _color(rects[0].get_facecolor()),
                 'orientation': 'horizontal' if horizontal else 'vertical'}
            if horizontal:
                s['category'] = [Y(r.get_y() + r.get_height() / 2) for r in rects]
                s['value'] = [_num(float(r.get_width())) for r in rects]
                base = [float(r.get_x()) for r in rects]
            else:
                s['category'] = [X(r.get_x() + r.get_width() / 2) for r in rects]
                s['value'] = [_num(float(r.get_height())) for r in rects]
                base = [float(r.get_y()) for r in rects]
            if any(abs(b) > 1e-12 for b in base):
                s['base'] = [_num(b) for b in base]
            _cap(s)
            series.append(s)
        elif isinstance(c, mpl.container.ErrorbarContainer):
            line, caps, bars = c.lines
            claimed.update(id(a) for a in (line, *caps, *bars) if a is not None)
            s = {'kind': 'errorbar', 'label': _label(c)}
            if line is not None:
                xd = np.asarray(line.get_xdata(orig=False), float)
                yd = np.asarray(line.get_ydata(orig=False), float)
                s |= {'color': _color(line.get_color()),
                      'x': [X(v) for v in xd], 'y': [Y(v) for v in yd]}
            else:
                # Bars without their points (`fmt='none'`, and every error bar
                # `bar` draws): each bar's position and range are all the
                # figure shows. A vertical bar stands at its point's x, a
                # horizontal one at its y, so with both the point is known.
                s |= {'color': _first_color(bars[0].get_colors()) if bars else None,
                      'note': 'error bars drawn without their points'}
            for coll in bars:
                segs = [np.asarray(sg, float) for sg in coll.get_segments()]
                if line is not None and len(segs) != len(xd):
                    continue
                if all(abs(sg[0][0] - sg[-1][0]) < 1e-12 for sg in segs):
                    if line is None:
                        s['x'] = [X(sg[0][0]) for sg in segs]
                    s['lo'] = [Y(min(sg[0][1], sg[-1][1])) for sg in segs]
                    s['hi'] = [Y(max(sg[0][1], sg[-1][1])) for sg in segs]
                else:
                    if line is None:
                        s['y'] = [Y(sg[0][1]) for sg in segs]
                    s['xlo'] = [X(min(sg[0][0], sg[-1][0])) for sg in segs]
                    s['xhi'] = [X(max(sg[0][0], sg[-1][0])) for sg in segs]
            if 'x' not in s and 'y' not in s:
                continue   # neither points nor bars: nothing was drawn
            _cap(s)
            series.append(s)

    for ln in ax.get_lines():
        if id(ln) in claimed or not ln.get_visible():
            continue
        xd = np.asarray(ln.get_xdata(orig=False), float)
        yd = np.asarray(ln.get_ydata(orig=False), float)
        if ln.get_transform() is ax.transData:
            if len(xd) == 0:
                continue
            drawn = ln.get_linestyle() not in ('None', ' ', '') and ln.get_linewidth() > 0
            s = {'kind': 'line' if drawn else 'points', 'label': _label(ln),
                 'color': _color(ln.get_color()),
                 'x': [X(v) for v in xd], 'y': [Y(v) for v in yd]}
            if getattr(ln, '_figtools_refcurve', False):
                s['role'] = 'guide'
            _cap(s)
            series.append(s)
        elif len(xd) >= 2 and np.allclose(yd, yd[0]) and xd.min() >= 0 and xd.max() <= 1:
            rules.append({'axis': 'y', 'value': Y(yd[0]), '_raw': float(yd[0])})
        elif len(yd) >= 2 and np.allclose(xd, xd[0]) and yd.min() >= 0 and yd.max() <= 1:
            rules.append({'axis': 'x', 'value': X(xd[0]), '_raw': float(xd[0])})
        else:
            missing.append('a line drawn outside data coordinates')

    for coll in ax.collections:
        if id(coll) in claimed or not coll.get_visible():
            continue
        if isinstance(coll, mpl.collections.PathCollection):
            off = np.asarray(coll.get_offsets(), float)
            if len(off) == 0:
                continue
            faces = np.atleast_2d(np.asarray(coll.get_facecolors()))
            cols = {_color(c) for c in faces} if faces.size else set()
            if not cols or cols == {None}:
                edges = np.atleast_2d(np.asarray(coll.get_edgecolors()))
                cols = {_color(c) for c in edges} if edges.size else set()
            s = {'kind': 'points', 'label': _label(coll),
                 'color': cols.pop() if len(cols) == 1 else None,
                 'x': [X(v) for v in off[:, 0]], 'y': [Y(v) for v in off[:, 1]]}
            remarks = []
            if len(cols) > 1:
                remarks.append('colour varies by point; not digested')
            sizes = np.asarray(coll.get_sizes(), float)
            if len(sizes) > 1 and np.ptp(sizes) > 0:
                remarks.append('marker size varies by point; not digested')
            if remarks:
                s['note'] = '; '.join(remarks)
            _cap(s)
            series.append(s)
        elif isinstance(coll, mpl.collections.LineCollection):
            if _first_color(coll.get_colors()) == _color(GREY['grid']):
                continue   # dot-plot stems: furniture in the grid's grey, not data
            at, lo, hi, orient, other = [], [], [], set(), []
            for sg in coll.get_segments():
                sg = np.asarray(sg, float)
                if len(sg) < 2 or not np.isfinite(sg).all():
                    continue
                (x0, y0), (x1, y1) = sg[0], sg[-1]
                if abs(x0 - x1) < 1e-12:
                    orient.add('vertical')
                    at.append(X(x0))
                    lo.append(Y(min(y0, y1)))
                    hi.append(Y(max(y0, y1)))
                elif abs(y0 - y1) < 1e-12:
                    orient.add('horizontal')
                    at.append(Y(y0))
                    lo.append(X(min(x0, x1)))
                    hi.append(X(max(x0, x1)))
                else:
                    other.append([X(x0), Y(y0), X(x1), Y(y1)])
            if not at and not other:
                continue
            s = {'kind': 'intervals', 'label': _label(coll),
                 'color': _first_color(coll.get_colors()),
                 'orientation': orient.pop() if len(orient) == 1 else 'mixed'}
            if at:
                s['at'], s['lo'], s['hi'] = at, lo, hi
            if other:
                s['segments'] = other
            _cap(s)
            series.append(s)
        elif isinstance(coll, mpl.collections.PolyCollection):
            for path in coll.get_paths():
                v = np.asarray(path.vertices, float)
                v = v[np.isfinite(v).all(axis=1)]
                if len(v) == 0:
                    continue
                cols = {}
                for x, y in v:
                    cols.setdefault(round(float(x), 9), []).append(float(y))
                keys = sorted(cols)
                s = {'kind': 'band', 'label': _label(coll),
                     'color': _first_color(coll.get_facecolors()),
                     'x': [X(k) for k in keys],
                     'lo': [Y(min(cols[k])) for k in keys],
                     'hi': [Y(max(cols[k])) for k in keys]}
                _cap(s)
                series.append(s)
        else:
            missing.append(f'{type(coll).__name__} not digested; see the image')

    for im in ax.images:
        if not im.get_visible():
            continue
        a = np.ma.filled(np.ma.asarray(im.get_array(), dtype=float), np.nan)
        entry = {'shape': list(a.shape), 'extent': [_num(float(e)) for e in im.get_extent()]}
        if np.isfinite(a).any():
            entry['min'] = _num(float(np.nanmin(a)))
            entry['max'] = _num(float(np.nanmax(a)))
        if a.ndim == 2 and a.size <= 400:
            entry['values'] = [[None if not np.isfinite(v) else _num(float(v)) for v in row]
                               for row in a]
        else:
            missing.append(f'image grid of shape {list(a.shape)} not digested; see the image')
        images.append(entry)

    loose = []
    for pa in ax.patches:
        if id(pa) in claimed or not pa.get_visible():
            continue
        tr = mpl.artist.Artist.get_transform(pa)
        if isinstance(pa, mpl.patches.Rectangle):
            if tr is ax.get_xaxis_transform(which='grid'):
                x0, x1 = sorted((float(pa.get_x()), float(pa.get_x() + pa.get_width())))
                shades.append({'axis': 'x', 'from': X(x0), 'to': X(x1), '_lo': x0, '_hi': x1})
                continue
            if tr is ax.get_yaxis_transform(which='grid'):
                y0, y1 = sorted((float(pa.get_y()), float(pa.get_y() + pa.get_height())))
                shades.append({'axis': 'y', 'from': Y(y0), 'to': Y(y1), '_lo': y0, '_hi': y1})
                continue
            if tr is ax.transData:
                loose.append(pa)
                continue
        missing.append(f'{type(pa).__name__} not digested; see the image')
    if loose:
        s = {'kind': 'bars', 'label': None, 'color': _color(loose[0].get_facecolor()),
             'orientation': 'vertical',
             'category': [X(r.get_x() + r.get_width() / 2) for r in loose],
             'value': [_num(float(r.get_height())) for r in loose],
             'note': 'rectangles drawn without a bar container, read as vertical bars'}
        base = [float(r.get_y()) for r in loose]
        if any(abs(b) > 1e-12 for b in base):
            s['base'] = [_num(b) for b in base]
        _cap(s)
        series.append(s)

    ylabels, xlabels = [], []
    for t in ax.texts:
        if not t.get_visible():
            continue
        txt = ' '.join(str(t.get_text()).split())
        # Direct line labels (label_lines) repeat the series names.
        if not txt or getattr(t, '_figtools_text', None) is not None:
            continue
        color = _color(t.get_color())
        if getattr(t, '_figtools_key', False):
            # An entry of `key`: the only annotation whose colour names a
            # series. `_names` marks such notes until the naming below.
            notes.append({'text': txt, 'color': color, '_names': True})
        elif isinstance(t, mpl.text.Annotation):
            xc = t.xycoords
            x, y = t.xy
            if xc == 'data':
                points.append({'text': txt, 'x': X(x), 'y': Y(y), 'color': color})
            elif isinstance(xc, tuple) and tuple(xc) == ('axes fraction', 'data'):
                ylabels.append((float(y), txt))
            elif isinstance(xc, tuple) and tuple(xc) == ('data', 'axes fraction'):
                xlabels.append((float(x), txt))
            else:
                notes.append({'text': txt, 'color': color})
        elif t.get_transform() is ax.transData:
            x, y = t.get_position()
            points.append({'text': txt, 'x': X(x), 'y': Y(y), 'color': color})
        else:
            notes.append({'text': txt, 'color': color})

    # Text drawn inside other artists: a key packed from offset boxes,
    # `AnchoredText`, an annotation box. A box pinned to a data point names
    # that point, as an annotation does. The lines of a box that packs its
    # text are a key's entries; a lone text, such as a panel letter in
    # `AnchoredText`, is a note that names nothing.
    for a in ax.artists:
        if not a.get_visible():
            continue
        lines = [n for n in map(_line_note, _text_lines(a)) if n['text']]
        if not lines:
            continue
        if isinstance(a, mpl.offsetbox.AnnotationBbox) and a.xycoords == 'data':
            x, y = a.xy
            colors = {n.get('color') for n in lines}
            points.append({'text': ' '.join(n['text'] for n in lines), 'x': X(x), 'y': Y(y),
                           'color': colors.pop() if len(colors) == 1 else None})
        else:
            if _packs(a):
                for n in lines:
                    n['_names'] = True
            notes += lines
    # A legend's entries are the labels of the series they stand for, which
    # the series already carry; an entry naming no series is a note in its
    # handle's colour, and names the unlabelled series drawn in it, since
    # the handle is drawn from that series.
    leg = ax.get_legend()
    if leg is not None and leg.get_visible():
        title = ' '.join(leg.get_title().get_text().split())
        if title and leg.get_title().get_visible():
            notes.append({'text': title})
        labels = {' '.join(s['label'].split()) for s in series if s.get('label')}
        for t, h in zip(leg.get_texts(), leg.legend_handles, strict=False):
            txt = ' '.join(t.get_text().split())
            if txt and t.get_visible() and txt not in labels:
                notes.append({'text': txt, 'color': _handle_color(h), '_names': True})

    def near(a, b):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))

    for value, txt in ylabels:
        rule = next((r for r in rules if r['axis'] == 'y' and near(r['_raw'], value)), None)
        if rule is not None:
            rule['label'] = txt
        else:
            notes.append({'text': txt})
    for value, txt in xlabels:
        shade = next((s for s in shades if s['axis'] == 'x'
                      and s['_lo'] - 1e-9 <= value <= s['_hi'] + 1e-9), None)
        rule = None if shade else next(
            (r for r in rules if r['axis'] == 'x' and near(r['_raw'], value)), None)
        target = shade or rule
        if target is not None:
            target['label'] = txt
        else:
            notes.append({'text': txt})

    # A key's entries name series by colour; carry the names onto the series.
    named = {n['color']: n['text'] for n in notes if n.get('_names') and n.get('color')}
    for s in series:
        if s['label'] is None and s.get('color') in named:
            s['label'] = named[s['color']]
            s['named_by'] = 'key'
    # A band drawn in a line's colour is that line's uncertainty.
    for s in series:
        if s['kind'] == 'band' and s['label'] is None:
            m = next((t for t in series if t['kind'] in ('line', 'points')
                      and t.get('label') and t.get('color') == s.get('color')), None)
            if m is not None:
                s['label'] = f'band: {m["label"]}'

    for r in rules:
        r.pop('_raw', None)
    for s in shades:
        s.pop('_lo', None)
        s.pop('_hi', None)
    for n in notes:
        n.pop('_names', None)
    for key, val in (('series', series), ('rules', rules), ('shades', shades),
                     ('points', points), ('notes', notes), ('images', images),
                     ('incomplete', missing)):
        if val:
            p[key] = val
    return p


def describe(fig):
    """The figure's plotted content as a JSON-ready dict (see the module doc)."""
    panels, seen = [], {}
    d = {'v': VERSION,
         'size_in': [round(float(v), 3) for v in fig.get_size_inches()],
         'panels': panels}
    sup = getattr(fig, '_suptitle', None)
    if sup is not None and sup.get_text().strip():
        d['title'] = ' '.join(sup.get_text().split())
    for ax in fig.axes:
        if not ax.get_visible() or ax.get_label() == '<colorbar>':
            continue
        key = tuple(round(float(v), 4) for v in ax.get_position().bounds)
        p = _panel(ax)
        p['index'] = len(panels)
        if key in seen:
            p['twin_of'] = seen[key]
        else:
            seen[key] = p['index']
        panels.append(p)
    return d


# --- carrying the digest in a PNG ---------------------------------------------

def encode(d):
    """A digest as the ASCII string stored in the PNG: zlib over compact JSON, base64."""
    raw = json.dumps(d, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return base64.b64encode(zlib.compress(raw, 9)).decode('ascii')


def decode(s):
    return json.loads(zlib.decompress(base64.b64decode(s)).decode('utf-8'))


def png_text(data):
    """The text chunks of a PNG as a dict (tEXt, zTXt and iTXt)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    out, i = {}, 8
    while i + 8 <= len(data):
        (n,) = struct.unpack('>I', data[i:i + 4])
        tag, body = data[i + 4:i + 8], data[i + 8:i + 8 + n]
        i += 12 + n
        if tag == b'tEXt':
            k, _, v = body.partition(b'\x00')
            out[k.decode('latin-1')] = v.decode('latin-1')
        elif tag == b'zTXt':
            k, _, rest = body.partition(b'\x00')
            out[k.decode('latin-1')] = zlib.decompress(rest[1:]).decode('latin-1')
        elif tag == b'iTXt':
            k, _, rest = body.partition(b'\x00')
            compressed, rest = rest[0], rest[2:]
            _lang, _, rest = rest.partition(b'\x00')
            _translated, _, text = rest.partition(b'\x00')
            out[k.decode('latin-1')] = (zlib.decompress(text) if compressed else text).decode('utf-8')
        elif tag == b'IEND':
            break
    return out


def from_png(data):
    """The digest a PNG carries, or None when it has none."""
    text = png_text(data).get(KEY)
    return decode(text) if text else None


def pdf_metadata(d):
    """The metadata entries that carry a digest in a PDF's information dictionary.

    The digest goes under the same key as in a PNG, continued under `-2`,
    `-3`, ... when it is longer than one string some readers accept.
    """
    text = encode(d)
    pieces = [text[i:i + PDF_PIECE] for i in range(0, len(text), PDF_PIECE)] or ['']
    meta = {KEY: pieces[0]}
    for n, piece in enumerate(pieces[1:], start=2):
        meta[f'{KEY}-{n}'] = piece
    return meta


@contextlib.contextmanager
def _quiet_pdf_metadata():
    """matplotlib warns about a metadata key it does not know; ours is deliberate."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='Unknown infodict keyword')
        yield


@contextlib.contextmanager
def _keep_layout(fig):
    """Leave a figure drawn without a layout engine without one.

    Saving switches the engine off and then restores it, and matplotlib
    restores "none" by reading the default, which `setup()` points at
    constrained layout: a figure whose axes box was placed by hand
    (`layout='none'`) would have it re-laid out on every later draw.
    """
    engine = fig.get_layout_engine()
    try:
        yield
    finally:
        if engine is None and fig.get_layout_engine() is not None:
            fig.set_layout_engine('none')


_PDF_ENTRY = re.compile(rb'/' + KEY.encode() + rb'(?:-(\d+))?\s*\(([A-Za-z0-9+/=]*)\)')


def pdf_digests(data):
    """Every digest a PDF carries, in file order.

    One for a figure file; one per figure for a document that embeds figure
    files, since the LaTeX engines Quarto uses copy each figure's information
    dictionary into the document. Reads the dictionaries as matplotlib writes
    them, plain objects with literal strings; a file rewritten with object
    streams by another tool hides them and reads as carrying nothing.
    """
    if not data.startswith(b'%PDF'):
        raise ValueError('not a PDF')
    groups = []
    for n, piece in _PDF_ENTRY.findall(data):
        if not n:
            groups.append({1: piece.decode('ascii')})
        elif groups:
            groups[-1][int(n)] = piece.decode('ascii')
        else:
            raise ValueError('a continuation piece of a digest precedes its first piece')
    out = []
    for g in groups:
        if set(g) != set(range(1, max(g) + 1)):
            raise ValueError('a digest in this PDF is missing a piece')
        out.append(decode(''.join(g[i] for i in sorted(g))))
    return out


def from_pdf(data):
    """The digest a PDF carries, the first when it holds several, or None."""
    found = pdf_digests(data)
    return found[0] if found else None


def from_image(data):
    """The digest a PNG or PDF carries, by the file's own signature."""
    if data.startswith(b'\x89PNG'):
        return from_png(data)
    if data.startswith(b'%PDF'):
        return from_pdf(data)
    raise ValueError('neither a PNG nor a PDF')


def _wrap_formatter(registry, kind):
    """Wrap the figure formatter in `registry` so its output carries the digest."""
    from matplotlib.figure import Figure
    try:
        current = registry.lookup_by_type(Figure)
    except KeyError:
        return False
    if getattr(current, '_figtools_digest', False):
        return True
    func = getattr(current, 'func', current)
    keywords = dict(getattr(current, 'keywords', None) or {})

    def with_digest(fig):
        kw = dict(keywords)
        meta = dict(kw.pop('metadata', None) or {})
        d = describe(fig)
        meta.update(pdf_metadata(d) if kind == 'pdf' else {KEY: encode(d)})
        with _quiet_pdf_metadata(), _keep_layout(fig):
            return func(fig, metadata=meta, **kw)

    with_digest._figtools_digest = True
    with_digest._figtools_original = current   # what `uninstall` puts back
    registry.for_type(Figure, with_digest)
    return True


def _shell():
    """The running IPython shell, or None outside IPython."""
    try:
        from IPython.core.getipython import get_ipython
    except ImportError:
        return None
    return get_ipython()


def _figure_formatters(ip):
    """(registry, its figure formatter) for the PNG and PDF registries that hold one."""
    from matplotlib.figure import Figure
    found = []
    for mime in ('image/png', 'application/pdf'):
        registry = ip.display_formatter.formatters[mime]
        try:
            found.append((registry, registry.lookup_by_type(Figure)))
        except KeyError:
            continue
    return found


def install():
    """Make every figure a notebook kernel displays carry its digest.

    Wraps the formatters that turn a figure into its PNG or PDF for display,
    adding the digest to the image's metadata. Does nothing, and returns
    False, outside IPython, in a shell with no kernel (terminal IPython,
    which keeps its window backend) or under a backend other than the
    inline one. Resolves the inline backend first when it is still pending:
    its configuration selects the figure formatters, and would replace the
    wrappers if it ran later, at the first figure.
    """
    try:
        import matplotlib_inline.backend_inline as inline
    except ImportError:
        return False
    ip = _shell()
    if ip is None or getattr(ip, 'kernel', None) is None:
        return False
    try:
        backend = mpl.get_backend(auto_select=False)
    except TypeError:   # matplotlib before 3.10
        backend = dict.__getitem__(mpl.rcParams, 'backend')
    if backend is not None and 'inline' not in str(backend):
        return False
    if getattr(inline.configure_inline_support, 'current_backend', 'unset') != 'inline':
        ip.enable_matplotlib('inline')
    formatters = ip.display_formatter.formatters
    done = [_wrap_formatter(formatters['image/png'], 'png'),
            _wrap_formatter(formatters['application/pdf'], 'pdf')]
    if not any(done):
        warnings.warn('figtools.digest: figures are displayed as neither PNG nor PDF '
                      '(fig-format svg?), so they will carry no digest', stacklevel=2)
        return False
    return True


def installed():
    """Whether the PNG or PDF figure formatter of the running shell carries
    the digest; False outside IPython."""
    ip = _shell()
    if ip is None:
        return False
    return any(getattr(f, '_figtools_digest', False) for _, f in _figure_formatters(ip))


def uninstall():
    """Put back the figure formatters `install()` wrapped; return whether any was.

    Only the digest goes: the inline backend `install()` resolved stays.
    Does nothing, and returns False, outside IPython or when no formatter
    carries the digest.
    """
    from matplotlib.figure import Figure
    ip = _shell()
    if ip is None:
        return False
    changed = False
    for registry, f in _figure_formatters(ip):
        if getattr(f, '_figtools_digest', False):
            registry.for_type(Figure, f._figtools_original)
            changed = True
    return changed


# --- writing a digest out -----------------------------------------------------

def _fmt(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f'{v:,}' if abs(v) >= 10000 else str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return ''
        return f'{v:,.0f}' if abs(v) >= 10000 else f'{v:.4g}'
    return str(v)


def _table(header, rows, note=None):
    """Markdown table lines; rows thinned to about TABLE_CELLS cells with a note."""
    n, c = len(rows), len(header)
    if n * c > TABLE_CELLS and n > 2:
        keep = max(2, TABLE_CELLS // c)
        idx = {0, n - 1}
        for j in range(c):
            nums = [(v, i) for i, v in enumerate(r[j] for r in rows)
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                    and not (isinstance(v, float) and math.isnan(v))]
            if nums:
                idx.add(min(nums)[1])
                idx.add(max(nums)[1])
        step = n / keep
        idx.update(round(i * step) for i in range(keep))
        idx = sorted(i for i in idx if 0 <= i < n)
        rows = [rows[i] for i in idx]
        note = ((note + ' ') if note else '') + (
            f'{n} rows thinned to {len(idx)}: first, last, each column\'s extremes and '
            f'every {step:.0f}th; the CSV holds every row.')

    def esc(s):
        return str(s).replace('|', '\\|')

    lines = ['| ' + ' | '.join(esc(h) for h in header) + ' |', '|' + '---|' * c]
    lines += ['| ' + ' | '.join(esc(_fmt(v)) for v in r) + ' |' for r in rows]
    if note:
        lines += ['', f'*{note}*']
    return lines


def _name(s, series):
    if s.get('label'):
        return s['label']
    k = [t for t in series if not t.get('label')].index(s) + 1
    return f'unlabelled {s["kind"]} {k}'


def _axis_desc(p, name):
    """'x: label, log scale' when the axis says something; None when it is bare."""
    bits = [p.get(f'{name}label')] if p.get(f'{name}label') else []
    if p.get(f'{name}scale'):
        bits.append(f'{p[name + "scale"]} scale')
    return f'{name}: ' + ', '.join(bits) if bits else None


def _panel_md(p, k):
    head = f'**Panel {k + 1}**'
    if p.get('twin_of') is not None:
        head += f' (second axis over panel {p["twin_of"] + 1})'
    bits = [f'title "{p["title"]}"'] if p.get('title') else []
    bits += [b for b in (_axis_desc(p, 'x'), _axis_desc(p, 'y')) if b]
    lines = [head + (': ' + '; '.join(bits) if bits else '') + '.', '']
    series = p.get('series', [])
    xname = p.get('xlabel') or 'x'
    yname = p.get('ylabel') or 'y'

    # Series sharing the same x go into one wide table; error bars drawn
    # without their points have no y to put in it.
    groups, done = {}, set()
    for s in series:
        if s['kind'] in ('line', 'points', 'errorbar', 'band') and 'x' in s \
                and ('y' in s or s['kind'] == 'band'):
            groups.setdefault(json.dumps(s['x']), []).append(s)
    for grp in groups.values():
        if len(grp) < 2:
            continue
        x = grp[0]['x']
        header, cols = [xname], []
        for s in grp:
            name = _name(s, series)
            if s['kind'] == 'band':
                header += [f'{name} lo', f'{name} hi']
                cols += [s['lo'], s['hi']]
            else:
                header.append(name)
                cols.append(s['y'])
                if 'lo' in s:
                    header += [f'{name} lo', f'{name} hi']
                    cols += [s['lo'], s['hi']]
            done.add(id(s))
        rows = [[x[i]] + [c[i] for c in cols] for i in range(len(x))]
        lines += _table(header, rows) + ['']
        thinned = [f'{_name(s, series)}: {s["thinned"]}' for s in grp if s.get('thinned')]
        if thinned:
            lines += ['*In the digest itself, ' + '; '.join(thinned) + '.*', '']

    for s in series:
        if id(s) in done:
            continue
        name, kind = _name(s, series), s['kind']
        if kind == 'errorbar' and 'y' not in s:
            header, cols = [xname, 'lo', 'hi'], [s['x'], s['lo'], s['hi']]
        elif kind == 'errorbar' and 'x' not in s:
            header, cols = [yname, 'x lo', 'x hi'], [s['y'], s['xlo'], s['xhi']]
        elif kind in ('line', 'points', 'errorbar') and all(isinstance(v, str) for v in s['y']) \
                and not all(isinstance(v, str) for v in s['x']):
            # Categories on y, values on x (a dot plot): the category names the row.
            header, cols = [yname, name], [s['y'], s['x']]
        elif kind in ('line', 'points', 'errorbar'):
            header, cols = [xname, name], [s['x'], s['y']]
            if 'lo' in s:
                header += ['lo', 'hi']
                cols += [s['lo'], s['hi']]
            if 'xlo' in s:
                header += ['x lo', 'x hi']
                cols += [s['xlo'], s['xhi']]
        elif kind == 'band':
            header, cols = [xname, f'{name} lo', f'{name} hi'], [s['x'], s['lo'], s['hi']]
        elif kind == 'bars':
            cat = (p.get('ylabel') if s['orientation'] == 'horizontal' else p.get('xlabel')) or 'category'
            header, cols = [cat, name], [s['category'], s['value']]
            if 'base' in s:
                header.append('base')
                cols.append(s['base'])
        elif kind == 'intervals' and 'at' in s:
            along = 'x' if s['orientation'] == 'vertical' else 'y'
            header, cols = [along, 'from', 'to'], [s['at'], s['lo'], s['hi']]
        elif kind == 'intervals':
            header = ['x0', 'y0', 'x1', 'y1']
            cols = [list(c) for c in zip(*s['segments'])]
        else:
            continue
        desc = f'{name} ({kind}'
        if kind in ('bars', 'intervals'):
            desc += f', {s["orientation"]}'
        if s.get('role') == 'guide':
            desc += ', reference curve'
        if s.get('named_by') == 'key':
            desc += ', named by the key'
        desc += ')'
        rows = [[c[i] for c in cols] for i in range(len(cols[0]))]
        lines += [desc + ':', ''] + _table(header, rows, s.get('note')) + ['']
        if s.get('thinned'):
            lines += [f'*In the digest itself, {s["thinned"]}.*', '']

    def where(v, label):
        return _fmt(v) + (f' "{label}"' if label else '')

    if p.get('rules'):
        lines.append('Reference lines: ' + '; '.join(
            f'{r["axis"]} = ' + where(r['value'], r.get('label')) for r in p['rules']) + '.')
    if p.get('shades'):
        lines.append('Shaded: ' + '; '.join(
            f'{s["axis"]} from {_fmt(s["from"])} to ' + where(s['to'], s.get('label'))
            for s in p['shades']) + '.')
    pts = p.get('points', [])
    if pts and len(pts) <= 12:
        lines.append('Labelled points: ' + '; '.join(
            f'{q["text"]} ({_fmt(q["x"])}, {_fmt(q["y"])})' for q in pts) + '.')
    elif pts:
        lines += ['Labelled points:', ''] + _table(
            ['text', 'x', 'y'], [[q['text'], q['x'], q['y']] for q in pts]) + ['']
    if p.get('notes'):
        lines.append('Notes: ' + '; '.join(n['text'] for n in p['notes']) + '.')
    for im in p.get('images', []):
        span = f', values from {im["min"]} to {im["max"]}' if 'min' in im else ''
        lines.append(f'Image grid {"x".join(str(v) for v in im["shape"])}{span}, '
                     f'extent {im["extent"]}' + (':' if 'values' in im else '.'))
        if 'values' in im:
            lines += [''] + _table([f'c{j + 1}' for j in range(len(im['values'][0]))],
                                   im['values']) + ['']
    if p.get('incomplete'):
        lines.append('Not digested: ' + '; '.join(p['incomplete']) + '.')
    if lines[-1] != '':
        lines.append('')
    return lines


def markdown(d, label, caption=None, name=None, figure_caption=None, ext='png'):
    """A digest as markdown lines: the heading, then one block per panel."""
    lines = [f'### {label}' + (f'. {caption}' if caption else ''), '']
    if figure_caption and figure_caption != caption:
        lines += [f'Part of: {figure_caption}', '']
    if d.get('title'):
        lines += [f'Figure title: {d["title"]}', '']
    for k, p in enumerate(d['panels']):
        lines += _panel_md(p, k)
    if name and ext:
        lines += [f'Files: {name}.csv (every value), {name}.{ext} (the image).', '']
    elif name:
        lines += [f'Files: {name}.csv (every value).', '']
    return lines


def csv_rows(d):
    """A digest as long-format rows: panel, series, kind, x, y, lo, hi, text."""
    rows = [('panel', 'series', 'kind', 'x', 'y', 'lo', 'hi', 'text')]
    for k, p in enumerate(d['panels']):
        for s in p.get('series', []):
            name, kind = s.get('label') or '', s['kind']
            if kind in ('line', 'points', 'errorbar', 'band'):
                # Error bars drawn without their points carry only the
                # positions their bars give: x for vertical bars, y for
                # horizontal ones.
                n = len(s['x'] if 'x' in s else s['y'])
                x, y = s.get('x', [None] * n), s.get('y', [None] * n)
                lo, hi = s.get('lo', [None] * n), s.get('hi', [None] * n)
                if 'x' in s:
                    rows += [(k + 1, name, kind, x[i], y[i], lo[i], hi[i], '') for i in range(n)]
                if 'xlo' in s:
                    rows += [(k + 1, name, 'errorbar x', x[i], y[i], s['xlo'][i], s['xhi'][i], '')
                             for i in range(n)]
            elif kind == 'bars':
                base = s.get('base', [None] * len(s['value']))
                rows += [(k + 1, name, f'bars {s["orientation"]}', s['category'][i], s['value'][i],
                          base[i], None, '') for i in range(len(s['value']))]
            elif kind == 'intervals':
                if 'at' in s:
                    vertical = s['orientation'] == 'vertical'
                    rows += [(k + 1, name, f'intervals {s["orientation"]}',
                              s['at'][i] if vertical else None, None if vertical else s['at'][i],
                              s['lo'][i], s['hi'][i], '') for i in range(len(s['at']))]
                for x0, y0, x1, y1 in s.get('segments', []):
                    rows.append((k + 1, name, 'segment', x0, y0, x1, y1, ''))
        for r in p.get('rules', []):
            rows.append((k + 1, r.get('label') or '', f'rule {r["axis"]}',
                         r['value'] if r['axis'] == 'x' else None,
                         r['value'] if r['axis'] == 'y' else None, None, None, r.get('label') or ''))
        for s in p.get('shades', []):
            rows.append((k + 1, s.get('label') or '', f'shade {s["axis"]}', None, None,
                         s['from'], s['to'], s.get('label') or ''))
        for q in p.get('points', []):
            rows.append((k + 1, '', 'point', q['x'], q['y'], None, None, q['text']))
    return rows


# --- reading a rendered page --------------------------------------------------

class _Page(HTMLParser):
    """The outputs of a rendered page in order: headings, printed text, tables, images.

    Reads only what is inside `<main>`; source code, the sidebar and the
    table of contents are skipped. An image remembers the nearest enclosing
    element whose id starts with `fig-` (Quarto's figure label) and the
    caption of each figure element that encloses it.

    `split` names the figures whose caption a non-figure output took over.
    Quarto turns a captioned cell holding more than one output into
    subfigures and numbers them (a), (b), ..., which is what a multi-panel
    figure wants and is wrong when the extra output is a repr or a table
    that happened to share the cell: the caption goes to whichever came
    first, and the figure is left without one. The signature is a subfloat
    holding no image, so a deliberate set of panels never trips it.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.items = []
        self.title = None
        self._text = None
        self._table = None
        self._row = None
        self._cell = None
        self._figures = []
        self._floats = []      # one frame per open <figure>, for `split`
        self.split = []
        self._in_main = False

    def _inside(self, cls):
        return any(cls in c for _, _, c in self.stack)

    def _fig_label(self):
        """The outermost enclosing Quarto figure label, or None.

        The outermost one is what the caption belongs to; a subfloat
        carries the same label with an index appended.
        """
        return next((x[1].get('id') for x in self.stack
                     if (x[1].get('id') or '').startswith('fig-')), None)

    def _output(self, kind):
        """Count one output against the innermost open <figure>."""
        if self._floats:
            self._floats[-1][kind] += 1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get('class') or '').split()
        if tag == 'main':
            self._in_main = True
        self.stack.append((tag, a, cls))
        if not self._in_main:
            return
        if tag == 'h1' and 'title' in cls:
            self._text = ('title', 0, [])
        elif tag in ('h1', 'h2', 'h3', 'h4') and not self._inside('quarto-title'):
            self._text = ('heading', int(tag[1]), [])
        elif tag == 'pre' and self._table is None and (
                self._inside('cell-output-stdout') or self._inside('cell-output-display')):
            self._text = ('text', 0, [])
        elif tag == 'table' and self._inside('cell-output'):
            self._table = []
        elif tag == 'tr' and self._table is not None:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = (tag == 'th', [])
        elif tag == 'img' and self._inside('cell-output'):
            fig_id = next((x[1].get('id') for x in reversed(self.stack)
                           if (x[1].get('id') or '').startswith('fig-')), None)
            self.items.append(('image', {'id': fig_id, 'src': a.get('src') or '',
                                         'captions': []}))
            for f in self._figures:
                f.append(len(self.items) - 1)
            self._output('image')
        elif tag == 'figure':
            self._figures.append([])
            self._floats.append({'sub': 'quarto-subfloat-fig' in cls,
                                 'label': self._fig_label(),
                                 'image': 0, 'other': 0})
        elif tag == 'figcaption':
            self._text = ('caption', 0, [])

    def handle_endtag(self, tag):
        for j in range(len(self.stack) - 1, -1, -1):
            if self.stack[j][0] == tag:
                del self.stack[j:]
                break
        if tag == 'main':
            self._in_main = False
        if self._text and tag in ('h1', 'h2', 'h3', 'h4', 'pre', 'figcaption'):
            kind, level, buf = self._text
            self._text = None
            text = ''.join(buf)
            if kind == 'title':
                self.title = ' '.join(text.split())
            elif kind == 'heading':
                self.items.append(('heading', level, ' '.join(text.split())))
            elif kind == 'text':
                self.items.append(('text', text.strip('\n')))
                self._output('other')
            elif kind == 'caption' and self._figures:
                for idx in self._figures[-1]:
                    self.items[idx][1]['captions'].append(' '.join(text.split()))
        if tag == 'figure' and self._figures:
            self._figures.pop()
            if self._floats:
                f = self._floats.pop()
                if f['sub'] and not f['image'] and f['other'] and f['label']:
                    self.split.append(f['label'])
        elif tag in ('td', 'th') and self._cell is not None and self._row is not None:
            is_header, buf = self._cell
            self._row.append((is_header, ' '.join(''.join(buf).split())))
            self._cell = None
        elif tag == 'tr' and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == 'table' and self._table is not None:
            if self._table:
                self.items.append(('table', self._table))
                self._output('other')
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[1].append(data)
        elif self._text is not None:
            self._text[2].append(data)


def _table_from_html(rows):
    """Markdown lines for a page table: its first row is the header."""
    width = max(len(r) for r in rows)
    cells = [[t for _, t in r] + [''] * (width - len(r)) for r in rows]
    header, body = cells[0], cells[1:]
    # A pandas frame with a named index adds a row holding only that name
    # under an empty corner cell; the name belongs in the corner.
    if body and not header[0] and body[0][0] and not any(body[0][1:]):
        header[0], body = body[0][0], body[1:]
    if not any(header):
        header = [f'c{j + 1}' for j in range(width)]

    def esc(s):
        return s.replace('|', '\\|')

    lines = ['| ' + ' | '.join(esc(h) for h in header) + ' |', '|' + '---|' * width]
    lines += ['| ' + ' | '.join(esc(c) for c in r) + ' |' for r in body]
    return lines


def _image(src, folder):
    """(extension, bytes) of a page's image, or a sentence saying why there are none.

    The image is embedded as a `data:` source, or linked by a path relative
    to the page's folder `folder`, which is how Quarto writes a figure
    unless the page embeds its resources: `<document>_files/figure-html/`
    beside the page. An absolute address is not fetched. The bytes' own
    signature says PNG or PDF; anything else is no figure this reads.
    """
    if not src:
        return 'Image without a source; nothing digested.'
    if src.startswith('data:'):
        head, _, body = src.partition(',')
        mime = head[len('data:'):].split(';')[0]
        if mime not in ('image/png', 'application/pdf'):
            return f'Image embedded as {mime or "untyped data"}, neither a PNG nor a PDF; nothing digested.'
        try:
            data = base64.b64decode(body)
        except (ValueError, TypeError):
            return 'Image embedded as data that is not base64; nothing digested.'
        what = f'Image embedded as {mime}'
    else:
        parts = urlsplit(src)
        if parts.scheme or parts.netloc or src.startswith('/'):
            return f'Image linked by the absolute address {src}, which is not fetched; nothing digested.'
        path = folder / unquote(parts.path)
        if not path.is_file():
            return f'Image file {path} not found; nothing digested.'
        data = path.read_bytes()
        what = f'Image file {path}'
    if data.startswith(b'\x89PNG'):
        return 'png', data
    if data.startswith(b'%PDF'):
        return 'pdf', data
    return f'{what} holds neither a PNG nor a PDF; nothing digested.'


def _write_csv(path, d):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerows([tuple('' if v is None else v for v in r) for r in csv_rows(d)])


# --- locating things ----------------------------------------------------------

def project_root(path):
    """The folder holding `_quarto.yml` above `path`; an error when there is none."""
    path = Path(path).resolve()
    for here in ([path] if path.is_dir() else []) + list(path.parents):
        if _is_project(here):
            return here
    raise FileNotFoundError(f'{path}: no _quarto.yml in this folder or above it')


def _is_project(folder):
    """Whether `folder` exists and holds a `_quarto.yml` (or `_quarto.yaml`)."""
    folder = Path(folder)
    return folder.is_dir() and any((folder / name).is_file() for name in QUARTO_FILES)


def _source_stem(output, output_dir=None, project=None):
    """Where an output's source sits, without its suffix (see `source_for`)."""
    output = Path(output)
    if (output_dir is None) != (project is None):
        raise ValueError('source_for: give output_dir and project together, or neither '
                         'to look beside the output')
    if output_dir is None:
        return output
    output_dir, project = Path(output_dir).resolve(), Path(project).resolve()
    try:
        return project / output.resolve().relative_to(output_dir)
    except ValueError:
        raise FileNotFoundError(f'{output}: not inside the output folder {output_dir}, '
                                f'so it has no source in {project}') from None


def _existing(stem, suffixes):
    """Those of the files `stem` names with each of `suffixes` that exist."""
    return [stem.with_suffix(s) for s in suffixes if stem.with_suffix(s).exists()]


def source_for(output, output_dir=None, project=None):
    """The document a rendered page or PDF was made from: its .py, .qmd or .ipynb.

    Quarto writes an output to the same path under the project's output
    folder (`output-dir`, a website's `_site`, a book's `_book`) as its
    source has under the project folder; give both folders to look there.
    Without them, or in a project with no output folder, where the two
    coincide, the source sits beside the output. Exactly one of the three
    files must exist.
    """
    stem = _source_stem(output, output_dir, project)
    found = _existing(stem, SOURCES)
    if len(found) != 1:
        raise FileNotFoundError(
            f'{output}: expected one source at {stem.with_suffix("")} with suffix '
            f'{", ".join(SOURCES)}; found {", ".join(f.name for f in found) or "none"}')
    return found[0]


def _skip(f, output, output_dir, project):
    """Why post-render leaves an output undigested, as the line it prints; None
    when the output is to be digested, or its missing source is an error.

    An output with no .py, .qmd or .ipynb source is skipped when a source
    that runs no figtools code made it, such as a website's plain Markdown
    page, or when it is a PDF, which a book writes from all its chapters at
    once. Several sources, or none at all for a page, are left to
    `source_for` to report.
    """
    stem = _source_stem(output, output_dir, project)
    if _existing(stem, SOURCES):
        return None
    prose = _existing(stem, PROSE_SOURCES)
    if Path(output).suffix.lower() == '.pdf':
        made = f'its source {prose[0].name} runs no figtools code' if prose else (
            'no single document made it, as with a book\'s PDF')
        return f'digest: skipped {f}: {made}; render-digest figure {f} prints the digests it carries'
    if prose:
        return f'digest: skipped {f}: its source {prose[0].name} runs no figtools code'
    return None


def cache_root():
    """The folder digests are filed under: the absolute folder the environment
    variable `FIGTOOLS_DIGEST_DIR` names, or `CACHE_ROOT` when it is unset."""
    value = os.environ.get('FIGTOOLS_DIGEST_DIR')
    if value is None:
        return CACHE_ROOT
    path = Path(value).expanduser()
    # A relative folder would move with the working directory, so `check`
    # run from elsewhere would look in a different place than the render wrote.
    if not value.strip() or not path.is_absolute():
        raise ValueError(f'figtools.digest: FIGTOOLS_DIGEST_DIR must name an absolute '
                         f'folder, got {value!r}; set it to one, or unset it to use {CACHE_ROOT}')
    return path


def digest_dir(root, source, cache=None):
    """Where a document's digest lands: `<cache>/<project folder name>/<document
    path without its suffix>`, the cache being `cache_root()` unless given."""
    cache = cache_root() if cache is None else Path(cache)
    rel = Path(source).resolve().relative_to(Path(root).resolve()).with_suffix('')
    return cache / Path(root).resolve().name / rel


# --- the extraction -----------------------------------------------------------

def _clear(out, root=None):
    """Empty a digest folder's files, keeping its subfolders (a PDF render's digest
    lives in `pdf/` under the page's).

    Refuses a folder this did not write, and one written for another project
    than `root` that still exists: digests are filed under the project
    folder's name, which two projects can share. A digest whose project
    has moved or gone, its folder no longer holding a `_quarto.yml`, is
    stale and is cleared. A manifest that records no project root, or a
    `root` of None, leaves nothing to compare.
    """
    out = Path(out)
    if out.exists():
        files = [p for p in out.iterdir() if p.is_file()]
        manifest = out / 'manifest.json'
        if files and not manifest.exists():
            raise FileExistsError(f'{out}: holds files and no manifest; refusing to clear it')
        if root is not None and manifest.exists():
            theirs = json.loads(manifest.read_text(encoding='utf-8')).get('project_root')
            ours = Path(root).resolve()
            if _other_project(theirs, ours):
                raise FileExistsError(
                    f'{out}: holds the digest of the project at {theirs}, not of {ours}; the '
                    f'two share a folder name. Set FIGTOOLS_DIGEST_DIR to another absolute '
                    f'folder when rendering one of them.')
        for p in files:
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)


def _other_project(theirs, ours):
    """Whether a manifest's project root `theirs` names a project other than
    `ours` that still exists.

    Path equality ignores case on Windows, and `ours` comes resolved, which
    collapses junctions, so one project reached by two routes matches
    itself. A root that no longer holds a `_quarto.yml` belongs to a project
    that moved or was deleted, and its digest is stale rather than another's.
    """
    return theirs is not None and Path(theirs) != Path(ours) and _is_project(theirs)


def _stamp(src, root, output):
    """(source path to print, its sha256, the output's render time)."""
    rel = src.resolve().relative_to(Path(root).resolve()).as_posix() if root else src.name
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    rendered = (datetime.fromtimestamp(Path(output).stat().st_mtime, tz=UTC)
                .astimezone().strftime('%Y-%m-%d %H:%M'))
    return rel, sha, rendered


def extract(html, out, source=None, root=None):
    """Write a rendered page's outputs to `out`; return (summary, split).

    `out` is emptied first, so a stale figure from an earlier render cannot
    survive; a folder that holds files and no manifest is refused, since it
    is not one this wrote, and so is one whose manifest names another
    project root than `root` that still exists (`_clear`). Each figure's
    image is read from the page, embedded in it or linked beside it
    (`_image`). `split` holds the labels of the figures whose
    caption another output in the same cell took over (`_Page`); a caller
    driving a render is expected to fail on it, not note it.
    """
    html, out = Path(html), Path(out)
    src = Path(source) if source else source_for(html)
    _clear(out, root)

    page = _Page()
    page.feed(html.read_text(encoding='utf-8'))
    page.close()
    rel, sha, rendered = _stamp(src, root, html)

    lines = [f'# Outputs of {rel}', '']
    if page.title:
        lines += [f'Title: {page.title}', '']
    lines += [(f'Rendered {rendered} from the source with sha256 {sha[:12]}. '
               f'`render-digest check {rel}` says whether the source changed since.'),
              ('Each figure below gives its plotted values (thinned tables here, every value '
               'in its CSV), then its reference lines, shaded spans and annotations; tables '
               'and printed output follow as they appeared, under their section.'), '']
    counts = {'figures': 0, 'tables': 0, 'text blocks': 0}
    figures, used = [], set()
    for item in page.items:
        if item[0] == 'heading':
            lines += ['#' * (item[1] + 1) + ' ' + item[2], '']
        elif item[0] == 'text':
            counts['text blocks'] += 1
            lines += ['```', item[1], '```', '']
        elif item[0] == 'table':
            counts['tables'] += 1
            lines += _table_from_html(item[1]) + ['']
        elif item[0] == 'image':
            counts['figures'] += 1
            info = item[1]
            label = info['id'] or f'fig-{counts["figures"]}'
            name = label
            while name in used:
                name = f'{label}-{counts["figures"]}'
            used.add(name)
            captions = info['captions']
            caption = captions[0] if captions else None
            outer = captions[1] if len(captions) > 1 else None
            image = _image(info['src'], html.resolve().parent)
            if isinstance(image, str):
                lines += [f'### {label}' + (f'. {caption}' if caption else ''), '', image, '']
                continue
            ext, data = image
            (out / f'{name}.{ext}').write_bytes(data)
            d = from_image(data)
            if d is None:
                lines += [f'### {label}' + (f'. {caption}' if caption else ''), '',
                          (f'No digest in this image (the figure was made without '
                           f'figtools.setup running); see {name}.{ext}.'), '']
                figures.append({'label': label, 'files': [f'{name}.{ext}'], 'digest': False})
                continue
            lines += markdown(d, label, caption, name, outer, ext)
            _write_csv(out / f'{name}.csv', d)
            figures.append({'label': label, 'files': [f'{name}.{ext}', f'{name}.csv'],
                            'digest': True})
    (out / 'outputs.md').write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    manifest = {'source': rel, 'sha256': sha, 'html': str(html), 'rendered': rendered,
                'written': datetime.now(tz=UTC).astimezone().isoformat(timespec='seconds'),
                'project_root': str(Path(root).resolve()) if root is not None else None,
                'counts': counts, 'figures': figures, 'split': page.split}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    summary = ', '.join(f'{v} {k[:-1] if v == 1 and k.endswith("s") else k}'
                        for k, v in counts.items())
    return summary, page.split


def extract_pdf(pdf, out, source=None, root=None):
    """Write the figures a rendered PDF document carries to `out`; return a summary.

    A PDF render yields its figures' digests and nothing else: captions,
    tables and printed output come from the digest of the same document's
    HTML render. The figures are numbered in document order.
    """
    pdf, out = Path(pdf), Path(out)
    src = Path(source) if source else source_for(pdf)
    _clear(out, root)
    digests = pdf_digests(pdf.read_bytes())
    rel, sha, rendered = _stamp(src, root, pdf)
    lines = [f'# Figures of {rel}, from its PDF render', '',
             (f'Rendered {rendered} from the source with sha256 {sha[:12]}. '
              f'`render-digest check {rel}` says whether the source changed since.'),
             ('A PDF render carries its figures and nothing else: their captions, the '
              'tables and the printed output are in the digest of the HTML render of the '
              'same document, one folder up.'), '']
    for k, d in enumerate(digests, start=1):
        name = f'figure-{k}'
        lines += markdown(d, name, None, name, None, None)
        _write_csv(out / f'{name}.csv', d)
    (out / 'outputs.md').write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    manifest = {'source': rel, 'sha256': sha, 'pdf': str(pdf), 'rendered': rendered,
                'written': datetime.now(tz=UTC).astimezone().isoformat(timespec='seconds'),
                'project_root': str(Path(root).resolve()) if root is not None else None,
                'counts': {'figures': len(digests)}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return f'{len(digests)} figure{"" if len(digests) == 1 else "s"} from the PDF'


SPLIT_ADVICE = (
    'A captioned figure cell produces the figure and nothing else; README.md '
    'carries the rule. The cell\'s last statement is displayed when it is an '
    'expression, and a matplotlib call such as a bare ax.set_xlabel(...) '
    'returns an object that shows as a second output, as does a table. Give a '
    'table a cell of its own, and end the figure cell with plt.show().')

# What Quarto passes a post-render script: the outputs, relative to the
# project folder, one per line; the project folder; its output folder, which
# is the project folder itself when the project names none.
_QUARTO_ENV = ('QUARTO_PROJECT_OUTPUT_FILES', 'QUARTO_PROJECT_DIR', 'QUARTO_PROJECT_OUTPUT_DIR')


def post_render(cache=None):
    """Quarto's post-render hook: digest every page or PDF this render produced.

    Finds each output's source through `source_for`, in the project folder at
    the path the output has under the output folder, and files the digest
    under `cache` (`cache_root()` unless given). An output made by no .py,
    .qmd or .ipynb, such as a plain Markdown page or a book's PDF, is
    skipped with a line saying why (`_skip`). Fails the render when a page
    holds a figure whose caption another output in the same cell took over:
    the page is written and digested first, so the message names what to fix
    and the evidence is on disk.
    """
    missing = [k for k in _QUARTO_ENV if not os.environ.get(k, '').strip()]
    if missing:
        raise SystemExit(f'render-digest post-render: {", ".join(missing)} '
                         f'{"is" if len(missing) == 1 else "are"} empty or unset; this runs as '
                         f'the post-render script of a Quarto project, which sets them')
    files = [f.strip() for f in os.environ['QUARTO_PROJECT_OUTPUT_FILES'].splitlines()
             if f.strip()]
    root = Path(os.environ['QUARTO_PROJECT_DIR']).resolve()
    output_dir = (root / os.environ['QUARTO_PROJECT_OUTPUT_DIR']).resolve()
    lost = {}
    for f in files:
        output = (root / f).resolve()
        kind = output.suffix.lower()
        if kind not in ('.html', '.pdf'):
            continue
        skipped = _skip(f, output, output_dir, root)
        if skipped:
            print(skipped)
            continue
        src = source_for(output, output_dir, root)
        out = digest_dir(root, src, cache)
        if kind == '.html':
            summary, split = extract(output, out, src, root)
            print(f'digest: {summary} -> {out}')
            if split:
                lost[f] = split
        else:
            print(f'digest: {extract_pdf(output, out / "pdf", src, root)} -> {out / "pdf"}')
    if lost:
        named = '; '.join(f'{f}: {", ".join(labs)}' for f, labs in lost.items())
        raise SystemExit(f'render-digest post-render: {named} lost its caption '
                         f'to another output of the same cell, and Quarto '
                         f'numbered the pair as subfigures. {SPLIT_ADVICE}')


def check(document, cache=None):
    """0 when the digest matches the document, 1 when the source changed, 2 when there is none.

    Reads the page's digest, or the PDF render's when the document has only
    been rendered to PDF, from `cache` (`cache_root()` unless given). A
    digest another project of the same folder name wrote is none while that
    project exists; once it has moved or gone, its digest is compared like
    any other, so a moved project's unchanged document reads as fresh.
    """
    doc = Path(document).resolve()
    root = project_root(doc.parent)
    folder = digest_dir(root, doc, cache)
    manifest = folder / 'manifest.json'
    if not manifest.exists() and (folder / 'pdf' / 'manifest.json').exists():
        folder, manifest = folder / 'pdf', folder / 'pdf' / 'manifest.json'
    if not manifest.exists():
        print(f'no digest for {doc.name} at {folder}; render the document')
        return 2
    info = json.loads(manifest.read_text(encoding='utf-8'))
    theirs = info.get('project_root')
    if _other_project(theirs, root):
        print(f'no digest for {doc.name}: {folder} holds the digest of the project at '
              f'{theirs}, which shares this project\'s folder name; set FIGTOOLS_DIGEST_DIR '
              f'to another absolute folder for one of them and render again')
        return 2
    if info['sha256'] != hashlib.sha256(doc.read_bytes()).hexdigest():
        print(f'stale: {doc.name} changed after the render of {info["rendered"]}; '
              f'render it again before trusting {folder / "outputs.md"}')
        return 1
    print(f'fresh: {folder / "outputs.md"} matches {doc.name} (rendered {info["rendered"]})')
    return 0


def _locate(a):
    """Run `where`, `check` or `extract` on the parsed arguments `a`; return the exit code."""
    if a.cmd == 'where':
        doc = a.document.resolve()
        print(digest_dir(project_root(doc.parent), doc))
        return 0
    if a.cmd == 'check':
        return check(a.document)
    src = a.source or source_for(a.html)
    root = project_root(src.parent)
    out = a.out or digest_dir(root, src)
    summary, split = extract(a.html, out, src, root)
    print(f'digest: {summary} -> {out}')
    if split:
        print(f'{", ".join(split)} lost its caption to another output of '
              f'the same cell. {SPLIT_ADVICE}', file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('post-render', help='digest the pages of the render that just finished')
    w = sub.add_parser('where', help='print the folder a document\'s digest lands in')
    w.add_argument('document', type=Path)
    c = sub.add_parser('check', help='exit 1 when the document changed since its digest, 2 with no digest')
    c.add_argument('document', type=Path)
    e = sub.add_parser('extract', help='digest one rendered page')
    e.add_argument('html', type=Path)
    e.add_argument('--source', type=Path, help='the document, when not the one file beside the page')
    e.add_argument('--out', type=Path, help='folder to write; default the cache location')
    g = sub.add_parser('figure', help='print the digest a PNG or PDF carries, as markdown')
    g.add_argument('images', type=Path, nargs='+')
    a = ap.parse_args(argv)

    if a.cmd == 'post-render':
        post_render()
    elif a.cmd in ('where', 'check', 'extract'):
        # A FIGTOOLS_DIGEST_DIR that names no usable folder leaves no digest
        # to read either. Checked on its own so that any other ValueError
        # still raises.
        try:
            cache_root()
        except ValueError as e:
            print(f'render-digest {a.cmd}: {e}', file=sys.stderr)
            return 2
        try:
            return _locate(a)
        except FileNotFoundError as e:
            # A document outside any Quarto project, or a file that is not
            # there: no digest to read, which is check's 2. The traceback's
            # exit code, 1, would read as "the source changed" to a gate.
            print(f'render-digest {a.cmd}: {e}', file=sys.stderr)
            return 2
    elif a.cmd == 'figure':
        missing = 0
        for image in a.images:
            data = image.read_bytes()
            found = pdf_digests(data) if data.startswith(b'%PDF') else [from_image(data)]
            found = [d for d in found if d is not None]
            if not found:
                print(f'{image}: no digest', file=sys.stderr)
                missing += 1
                continue
            for k, d in enumerate(found, start=1):
                label = image.stem if len(found) == 1 else f'{image.stem}, figure {k}'
                print('\n'.join(markdown(d, label)))
        return 1 if missing else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
