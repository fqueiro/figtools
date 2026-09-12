"""Machine-readable digests of figures, and text renderings of a document's outputs.

A rendered document shows its findings in figures and tables that a reader
sees and a program cannot: the source holds the code that made them, the
page holds the pixels. This module gives every figure a *digest*, the
plotted data as JSON, and after a render collects the digests, the tables
and the printed output of a document into one markdown file a program can
read.

Mechanism. `install()` (run by `setup()` in a notebook kernel) wraps the
formatter IPython uses to turn a figure into its PNG, so the PNG carries the
digest as a compressed text chunk under the key `plotsmith-digest`. The
image is otherwise unchanged and nothing else appears in the cell: a second
output in a captioned figure cell would turn the pair into subfigures.
After a render, `post_render()` (Quarto's `post-render` hook) reads each
embedded image back out of the page, writes the digests as markdown and CSV
beside the PNGs, converts the page's tables and printed output to text, and
stamps the lot with a hash of the source, so a reader can tell whether the
document changed since. Everything lands under
`~/.local/share/render-digests/<project>/<document path>/`, outside the
repository and outside Dropbox.

What a digest holds, per panel: the axis titles, scales and kinds (number,
date, category); each plotted series with its label, colour and values
(lines and marker series, bars, scatters, bands, intervals, error bars);
reference rules and shaded spans with their in-place labels; points named
in data coordinates; notes written in axes coordinates; small image grids in
full and larger ones by shape and range; and what was not digested. Series
longer than `SERIES_CAP` points are thinned evenly and say so. Values keep
six significant digits.

    render-digest post-render        # what `_quarto.yml` runs after a render
    render-digest where <document>   # the folder a document's digest lands in
    render-digest check <document>   # exit 1 when the source changed since the render
    render-digest extract <page.html> [--source <document>] [--out <folder>]
    render-digest png <figure.png>   # print the digest a PNG carries
"""

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import zlib
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import matplotlib as mpl
import matplotlib.category
import matplotlib.dates as mdates
import numpy as np

from plotsmith import GREY

KEY = 'plotsmith-digest'
VERSION = 1
CACHE_ROOT = Path.home() / '.local' / 'share' / 'render-digests'
SERIES_CAP = 5000   # points kept per series in a digest; longer ones are thinned evenly
TABLE_CELLS = 600   # cells a markdown table may hold before its rows are thinned


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


def _cap(s):
    """Thin a series longer than SERIES_CAP evenly, and say so."""
    n = len(s.get('x') or s.get('at') or s.get('category') or s.get('segments') or [])
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
            claimed.add(id(line))
            claimed.update(id(a) for a in caps)
            claimed.update(id(a) for a in bars)
            xd = np.asarray(line.get_xdata(orig=False), float)
            yd = np.asarray(line.get_ydata(orig=False), float)
            s = {'kind': 'errorbar', 'label': _label(c), 'color': _color(line.get_color()),
                 'x': [X(v) for v in xd], 'y': [Y(v) for v in yd]}
            for coll in bars:
                segs = [np.asarray(sg, float) for sg in coll.get_segments()]
                if len(segs) != len(xd):
                    continue
                if all(abs(sg[0][0] - sg[-1][0]) < 1e-12 for sg in segs):
                    s['lo'] = [Y(min(sg[0][1], sg[-1][1])) for sg in segs]
                    s['hi'] = [Y(max(sg[0][1], sg[-1][1])) for sg in segs]
                else:
                    s['xlo'] = [X(min(sg[0][0], sg[-1][0])) for sg in segs]
                    s['xhi'] = [X(max(sg[0][0], sg[-1][0])) for sg in segs]
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
            if getattr(ln, '_fs_guide', False):
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
        if not txt or getattr(t, '_fs_text', None) is not None:
            continue
        color = _color(t.get_color())
        if isinstance(t, mpl.text.Annotation):
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

    # A key (`key`) names series by colour; carry the names onto the series.
    named = {n['color']: n['text'] for n in notes if n.get('color')}
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


def install():
    """Make every figure a notebook kernel displays carry its digest.

    Wraps the formatter that turns a figure into its PNG for display, adding
    the digest to the image's metadata. Does nothing, and returns False,
    outside IPython or under a backend other than the inline one. Resolves
    the inline backend first when it is still pending: its configuration
    selects the figure formatters, and would replace this wrapper if it ran
    later, at the first figure.
    """
    try:
        import matplotlib_inline.backend_inline as inline
        from IPython.core.getipython import get_ipython
    except ImportError:
        return False
    ip = get_ipython()
    if ip is None:
        return False
    try:
        backend = mpl.get_backend(auto_select=False)
    except TypeError:   # matplotlib before 3.10
        backend = dict.__getitem__(mpl.rcParams, 'backend')
    if backend is not None and 'inline' not in str(backend):
        return False
    from matplotlib.figure import Figure
    if getattr(inline.configure_inline_support, 'current_backend', 'unset') != 'inline':
        ip.enable_matplotlib('inline')
    fmt = ip.display_formatter.formatters['image/png']
    try:
        current = fmt.lookup_by_type(Figure)
    except KeyError:
        raise RuntimeError('plotsmith.digest.install: no PNG formatter is registered '
                           'for figures; is the inline backend active?') from None
    if getattr(current, '_plotsmith_digest', False):
        return True
    func = getattr(current, 'func', current)
    keywords = dict(getattr(current, 'keywords', None) or {})

    def with_digest(fig):
        kw = dict(keywords)
        meta = dict(kw.pop('metadata', None) or {})
        meta[KEY] = encode(describe(fig))
        return func(fig, metadata=meta, **kw)

    with_digest._plotsmith_digest = True
    fmt.for_type(Figure, with_digest)
    return True


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

    # Series sharing the same x go into one wide table.
    groups, done = {}, set()
    for s in series:
        if s['kind'] in ('line', 'points', 'errorbar', 'band') and 'x' in s:
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
        if kind in ('line', 'points', 'errorbar') and all(isinstance(v, str) for v in s['y']) \
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
            desc += ', guide'
        if s.get('named_by') == 'key':
            desc += ', named by the key'
        desc += ')'
        rows = [[c[i] for c in cols] for i in range(len(cols[0]))]
        lines += [desc + ':', ''] + _table(header, rows, s.get('note')) + ['']
        if s.get('thinned'):
            lines += [f'*In the digest itself, {s["thinned"]}.*', '']

    def where(v, label):
        return f'{v}' + (f' "{label}"' if label else '')

    if p.get('rules'):
        lines.append('Reference lines: ' + '; '.join(
            f'{r["axis"]} = ' + where(r['value'], r.get('label')) for r in p['rules']) + '.')
    if p.get('shades'):
        lines.append('Shaded: ' + '; '.join(
            f'{s["axis"]} from {s["from"]} to ' + where(s['to'], s.get('label'))
            for s in p['shades']) + '.')
    pts = p.get('points', [])
    if pts and len(pts) <= 12:
        lines.append('Labelled points: ' + '; '.join(
            f'{q["text"]} ({q["x"]}, {q["y"]})' for q in pts) + '.')
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


def markdown(d, label, caption=None, name=None, figure_caption=None):
    """A digest as markdown lines: the heading, then one block per panel."""
    lines = [f'### {label}' + (f'. {caption}' if caption else ''), '']
    if figure_caption and figure_caption != caption:
        lines += [f'Part of: {figure_caption}', '']
    if d.get('title'):
        lines += [f'Figure title: {d["title"]}', '']
    for k, p in enumerate(d['panels']):
        lines += _panel_md(p, k)
    if name:
        lines += [f'Files: {name}.csv (every value), {name}.png (the image).', '']
    return lines


def csv_rows(d):
    """A digest as long-format rows: panel, series, kind, x, y, lo, hi, text."""
    rows = [('panel', 'series', 'kind', 'x', 'y', 'lo', 'hi', 'text')]
    for k, p in enumerate(d['panels']):
        for s in p.get('series', []):
            name, kind = s.get('label') or '', s['kind']
            if kind in ('line', 'points', 'errorbar', 'band'):
                n = len(s['x'])
                y = s.get('y', [None] * n)
                lo, hi = s.get('lo', [None] * n), s.get('hi', [None] * n)
                rows += [(k + 1, name, kind, s['x'][i], y[i], lo[i], hi[i], '') for i in range(n)]
                if 'xlo' in s:
                    rows += [(k + 1, name, 'errorbar x', s['x'][i], y[i], s['xlo'][i], s['xhi'][i], '')
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
        self._in_main = False

    def _inside(self, cls):
        return any(cls in c for _, _, c in self.stack)

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
        elif tag == 'figure':
            self._figures.append([])
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
            elif kind == 'caption' and self._figures:
                for idx in self._figures[-1]:
                    self.items[idx][1]['captions'].append(' '.join(text.split()))
        if tag == 'figure' and self._figures:
            self._figures.pop()
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


def _embedded_png(src):
    """The bytes of a `data:image/png;base64,...` source, else None."""
    head, sep, body = src.partition(',')
    if not sep or not head.startswith('data:image/png'):
        return None
    try:
        return base64.b64decode(body)
    except (ValueError, TypeError):
        return None


# --- locating things ----------------------------------------------------------

def project_root(path):
    """The folder holding `_quarto.yml` above `path`; an error when there is none."""
    path = Path(path).resolve()
    for here in ([path] if path.is_dir() else []) + list(path.parents):
        if (here / '_quarto.yml').exists() or (here / '_quarto.yaml').exists():
            return here
    raise FileNotFoundError(f'{path}: no _quarto.yml in this folder or above it')


def source_for(html):
    """The one document beside a rendered page: its .py, .qmd or .ipynb."""
    html = Path(html)
    found = [html.with_suffix(s) for s in ('.py', '.qmd', '.ipynb') if html.with_suffix(s).exists()]
    if len(found) != 1:
        raise FileNotFoundError(
            f'{html.name}: expected one source beside it, found '
            f'{", ".join(f.name for f in found) or "none"}')
    return found[0]


def digest_dir(root, source, cache=CACHE_ROOT):
    """Where a document's digest lands: the cache, the project, the document's path."""
    rel = Path(source).resolve().relative_to(Path(root).resolve()).with_suffix('')
    return Path(cache) / Path(root).resolve().name / rel


# --- the extraction -----------------------------------------------------------

def extract(html, out, source=None, root=None):
    """Write a rendered page's outputs to `out`; return a one-line summary.

    `out` is emptied first, so a stale figure from an earlier render cannot
    survive; a non-empty folder that holds no manifest is refused, since it
    is not one this wrote.
    """
    html, out = Path(html), Path(out)
    src = Path(source) if source else source_for(html)
    if out.exists():
        if any(out.iterdir()) and not (out / 'manifest.json').exists():
            raise FileExistsError(f'{out}: not empty and not a digest folder; refusing to clear it')
        shutil.rmtree(out)
    out.mkdir(parents=True)

    page = _Page()
    page.feed(html.read_text(encoding='utf-8'))
    page.close()
    rel = src.resolve().relative_to(Path(root).resolve()).as_posix() if root else src.name
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    rendered = (datetime.fromtimestamp(html.stat().st_mtime, tz=UTC)
                .astimezone().strftime('%Y-%m-%d %H:%M'))

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
            png = _embedded_png(info['src'])
            if png is None:
                lines += [f'### {label}' + (f'. {caption}' if caption else ''), '',
                          'Image not embedded as a PNG; nothing digested.', '']
                continue
            (out / f'{name}.png').write_bytes(png)
            d = from_png(png)
            if d is None:
                lines += [f'### {label}' + (f'. {caption}' if caption else ''), '',
                          (f'No digest in this image (the figure was made without '
                           f'plotsmith.setup running); see {name}.png.'), '']
                figures.append({'label': label, 'files': [f'{name}.png'], 'digest': False})
                continue
            lines += markdown(d, label, caption, name, outer)
            with open(out / f'{name}.csv', 'w', newline='', encoding='utf-8') as f:
                csv.writer(f).writerows(
                    [tuple('' if v is None else v for v in r) for r in csv_rows(d)])
            figures.append({'label': label, 'files': [f'{name}.png', f'{name}.csv'], 'digest': True})
    (out / 'outputs.md').write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    manifest = {'source': rel, 'sha256': sha, 'html': str(html), 'rendered': rendered,
                'written': datetime.now(tz=UTC).astimezone().isoformat(timespec='seconds'),
                'counts': counts, 'figures': figures}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return ', '.join(f'{v} {k[:-1] if v == 1 and k.endswith("s") else k}' for k, v in counts.items())


def post_render(cache=CACHE_ROOT):
    """Quarto's post-render hook: digest every page this render produced."""
    files = [f.strip() for f in os.environ.get('QUARTO_PROJECT_OUTPUT_FILES', '').splitlines()
             if f.strip()]
    if not files:
        raise SystemExit('render-digest post-render: QUARTO_PROJECT_OUTPUT_FILES is empty; '
                         'this runs as the post-render script of a Quarto project')
    root = Path.cwd()
    for f in files:
        html = (root / f).resolve()
        if html.suffix.lower() != '.html':
            continue
        src = source_for(html)
        out = digest_dir(root, src, cache)
        print(f'digest: {extract(html, out, src, root)} -> {out}')


def check(document, cache=CACHE_ROOT):
    """0 when the digest matches the document, 1 when the source changed, 2 when there is none."""
    doc = Path(document).resolve()
    root = project_root(doc.parent)
    folder = digest_dir(root, doc, cache)
    manifest = folder / 'manifest.json'
    if not manifest.exists():
        print(f'no digest for {doc.name} at {folder}; render the document')
        return 2
    info = json.loads(manifest.read_text(encoding='utf-8'))
    if info['sha256'] != hashlib.sha256(doc.read_bytes()).hexdigest():
        print(f'stale: {doc.name} changed after the render of {info["rendered"]}; '
              f'render it again before trusting {folder / "outputs.md"}')
        return 1
    print(f'fresh: {folder / "outputs.md"} matches {doc.name} (rendered {info["rendered"]})')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('post-render', help='digest the pages of the render that just finished')
    w = sub.add_parser('where', help='print the folder a document\'s digest lands in')
    w.add_argument('document', type=Path)
    c = sub.add_parser('check', help='exit 1 when the document changed since its digest')
    c.add_argument('document', type=Path)
    e = sub.add_parser('extract', help='digest one rendered page')
    e.add_argument('html', type=Path)
    e.add_argument('--source', type=Path, help='the document, when not the one file beside the page')
    e.add_argument('--out', type=Path, help='folder to write; default the cache location')
    g = sub.add_parser('png', help='print the digest a PNG carries, as markdown')
    g.add_argument('png', type=Path)
    a = ap.parse_args(argv)

    if a.cmd == 'post-render':
        post_render()
    elif a.cmd == 'where':
        doc = a.document.resolve()
        print(digest_dir(project_root(doc.parent), doc))
    elif a.cmd == 'check':
        return check(a.document)
    elif a.cmd == 'extract':
        src = a.source or source_for(a.html)
        root = project_root(src.parent)
        out = a.out or digest_dir(root, src)
        print(f'digest: {extract(a.html, out, src, root)} -> {out}')
    elif a.cmd == 'png':
        d = from_png(a.png.read_bytes())
        if d is None:
            print(f'{a.png}: no digest', file=sys.stderr)
            return 1
        print('\n'.join(markdown(d, a.png.stem)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
