"""Machine-readable digests of figures, and text renderings of a document's outputs.

A rendered document shows its findings in figures and tables that a reader
sees and a program cannot: the source holds the code that made them, the
page holds the pixels. This module gives every figure a *digest*, its
plotted data, and after a render collects the digests, the tables and the
printed output of a document into one markdown file a program can read.
No figure file carries a digest, so neither does a paper or a page that
includes the figure: each digest lives beside the figure's files or in a
store outside the project.

Figures `save` writes. Beside the PDFs, each call writes one plain-text
companion, `<path>.digest.txt` (`companion`): the figure's name; a line
`source: <file>:<line>` giving where `save` was called, the file relative
to the companion's folder, left out when the caller cannot be placed with
certainty; a `file:` line per PDF with its size; and the digest, with every
plotted value, one per line. The text is deterministic, and the file is
rewritten only when its text changes other than in the line number of the
call (`write_companion`), so it can be tracked in version control while
the PDFs are ignored. `find_companion` goes from a PDF to its companion:
`<name>.digest.txt` for `<name>.pdf`, the same file for a variant
`<name>_<target>.pdf`. Each PDF carries one entry figtools wrote, the
fingerprint `save` compares, in its document catalog, which LaTeX never
copies into a document that includes the figure.

Figures a notebook kernel displays. `install()` (run by `setup()`) wraps the
formatters a kernel uses to turn a figure into its PNG, JPEG, SVG or PDF.
Each returns the image unchanged, and files the figure's digest in the
store, `<cache>/_figures/<sha256>.json` (`remember`), under the sha256 of
the image's bytes: of the decoded image, or of an SVG's text as UTF-8,
which are the bytes a rendered page embeds or links, so the page's image
finds its digest again (`recall`). Terminal IPython runs no kernel, so
there `install()` leaves the shell and its window backend alone.
`uninstall()` puts back the formatters `install()` wrapped, and
`installed()` says whether the digest is armed. Nothing else appears in the
cell: a second output in a captioned figure cell would turn the pair into
subfigures.

A Quarto render to PDF deletes its figure files once LaTeX has included
them, so no image is left to hash. In a Quarto render of a document inside
a project, the PDF formatter therefore also records each figure's digest in
the order the figures are drawn, in a folder of the render's own,
`<document's digest folder>/_pdf_renders/<render>/`, with the sha256 of the
document's source (`_render_folder`). A render is told from the next by a
name the kernel keeps in the notebook's namespace, which Quarto clears with
`%reset` at the start and the end of every execution, also in a kernel its
daemon keeps between renders of the document; a notebook that runs `%reset`
itself splits its render in two. `setup()` opens the render's folder in a
render that displays figures as PDF, so a render that draws no figure
leaves an empty one.

After a render, `post_render()` (Quarto's `post-render` hook) finds the
source of each page or PDF the render wrote, at the same path under the
project folder as the output has under the output folder (`output-dir`, a
website's `_site`, a book's `_book`; without one, beside the output). An
output whose source runs no figtools code (a plain Markdown page) is
skipped with a line saying so, and so is a PDF no single document made (a
book's). It reads each figure's image out of the page, embedded in it or
linked beside it (Quarto's default writes `<document>_files/figure-html/`
next to the page), looks its digest up in the store by the image's hash,
writes the digests as markdown and CSV beside the images, converts the
page's tables and printed output to text, and stamps the lot with a hash of
the source, so a reader can tell whether the document changed since. For a
PDF it takes the newest recorded render whose source hash is the
document's, numbers its figures in drawing order, writes them under `pdf/`
in the document's folder, and removes the document's older renders.
Everything lands under `<cache>/<project folder name>/<document path>/`,
outside the repository, where the cache is `~/.local/share/render-digests`
or the absolute folder the environment variable `FIGTOOLS_DIGEST_DIR`
names, read at each call. Each folder's manifest records the project's
absolute path, and a render refuses to overwrite the digest of another
project with the same folder name while that project's folder still holds
its `_quarto.yml`; the variable gives one of them a cache of its own. The
digest of a project that moved or was deleted is stale, and the next render
replaces it. `prune` lists what has gone stale: the digests of documents
and projects that no longer exist, store entries unread for `STALE_DAYS`
days, and companions whose figure is gone.

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
Other notes, such as a panel letter, name no series. In the store, series
longer than `SERIES_CAP` points are thinned evenly and say so; a companion
keeps every point. Values keep six significant digits.

    render-digest post-render        # what `_quarto.yml` runs after a render
    render-digest where <document>   # the folder a document's digest lands in
    render-digest check <document>   # exit 1 when the source changed since the render, 2 with no digest
    render-digest extract <page.html> [--source <document>] [--out <folder>]
    render-digest figure <figure> ...   # a saved figure's companion, or an image's digest from the store
    render-digest prune [<project folder>] [--yes]   # list stale digests and companions, remove them
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
import shutil
import sys
import time
import warnings
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit

import matplotlib as mpl
import matplotlib.category
import matplotlib.dates as mdates
import matplotlib.offsetbox
import numpy as np

from figtools import GREY

VERSION = 1
CACHE_ROOT = Path.home() / '.local' / 'share' / 'render-digests'   # unless FIGTOOLS_DIGEST_DIR is set
SOURCES = ('.py', '.qmd', '.ipynb')   # the suffixes a rendered document's source may have
PROSE_SOURCES = ('.md', '.markdown', '.Rmd')   # sources of pages that run no figtools code
QUARTO_FILES = ('_quarto.yml', '_quarto.yaml')   # either marks a Quarto project's folder
SERIES_CAP = 5000   # points kept per series in the store; longer ones are thinned evenly
TABLE_CELLS = 600   # cells a markdown table may hold before its rows are thinned
FIGURES = '_figures'   # the store's folder under the cache, digests keyed by image hash
RENDERS = '_pdf_renders'   # under a document's digest folder, each PDF render's figures in order
COMPANION = '.digest.txt'   # the suffix of the companion `save` writes beside a figure's PDFs
STALE_DAYS = 30   # a store entry neither written nor read for this long is pruned
# The name, in a notebook's namespace, of the render the kernel is running.
# Leading underscore and pushed as hidden, so `%who` leaves it out.
_RENDER_NAME = '_figtools_render'
# The image formats a notebook kernel may display a figure in: IPython's
# MIME type, the kind of image, and the extension a page gives its file.
_FORMATS = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/svg+xml': 'svg',
            'application/pdf': 'pdf'}


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


def _cap(s, cap):
    """Thin a series longer than `cap` points evenly, and say so; None keeps
    every point."""
    n = len(s.get('x') or s.get('y') or s.get('at') or s.get('category')
            or s.get('segments') or [])
    if cap is None or n <= cap:
        return
    keep = np.unique(np.linspace(0, n - 1, cap).round().astype(int))
    for k, v in list(s.items()):
        if isinstance(v, list) and len(v) == n:
            s[k] = [v[i] for i in keep]
    s['thinned'] = f'{n} points, {len(keep)} kept evenly'


def _panel(ax, cap=SERIES_CAP):
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
            _cap(s, cap)
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
            _cap(s, cap)
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
            _cap(s, cap)
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
            _cap(s, cap)
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
            _cap(s, cap)
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
                _cap(s, cap)
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
        _cap(s, cap)
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


def describe(fig, cap=SERIES_CAP):
    """The figure's plotted content as a JSON-ready dict (see the module doc).

    A series longer than `cap` points is thinned evenly to `cap` and says
    so; `cap=None` keeps every point, as a companion does.
    """
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
        p = _panel(ax, cap)
        p['index'] = len(panels)
        if key in seen:
            p['twin_of'] = seen[key]
        else:
            seen[key] = p['index']
        panels.append(p)
    return d


# --- the store: digests of the figures a notebook kernel displays -----------

def _atomic_write(path, data):
    """Write the bytes `data` to `path` through a temporary file beside it,
    so a reader never finds half a file.

    The replace is retried for a moment while another program, such as a
    file sync, holds the file open, then raises.
    """
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_bytes(data)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def image_key(data):
    """The store's key for an image: the sha256 of its bytes."""
    return hashlib.sha256(data).hexdigest()


def _store_entry(data, cache=None):
    cache = cache_root() if cache is None else Path(cache)
    return cache / FIGURES / f'{image_key(data)}.json'


def remember(d, data, kind, cache=None):
    """File the digest `d` of the image whose bytes are `data` (kind 'png',
    'jpeg', 'svg' or 'pdf') in the store, under the image's key; return the
    entry's path."""
    path = _store_entry(data, cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {'v': VERSION, 'kind': kind, 'digest': d}
    _atomic_write(path, json.dumps(entry, separators=(',', ':')).encode('utf-8'))
    return path


def recall(data, cache=None, touch=False):
    """The digest the store holds for the image whose bytes are `data`, or
    None. `touch` marks the entry as read now, which `prune` measures its
    age from."""
    path = _store_entry(data, cache)
    try:
        entry = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if touch:
        os.utime(path)
    return entry['digest']


def _source_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _render_folder(ip):
    """The folder of the PDF-format figures of the Quarto render this kernel
    runs, opened on first use; None outside a Quarto render of a document in
    a project, where no post-render hook would read it.

    The render is named in the notebook's namespace (`_RENDER_NAME`), which
    Quarto clears at the start and end of each execution, so the next
    render, in this kernel or a new one, opens a folder of its own. The
    name is the time it opened, in nanoseconds, then the process id, so
    the newest folder sorts last.
    """
    import figtools
    doc = figtools._quarto_document()
    if doc is None or not doc.is_file():
        return None
    try:
        root = project_root(doc.parent)
    except FileNotFoundError:
        return None
    renders = digest_dir(root, doc) / RENDERS
    name = ip.user_ns.get(_RENDER_NAME)
    if isinstance(name, str) and (renders / name).is_dir():
        return renders / name
    stamp = time.time_ns()
    while (renders / f'{stamp}-{os.getpid()}').exists():   # two renders within the clock's tick
        stamp += 1
    name = f'{stamp}-{os.getpid()}'
    folder = renders / name
    folder.mkdir(parents=True)
    info = {'document': str(doc.resolve()), 'project_root': str(root),
            'sha256': _source_sha(doc),
            'started': datetime.now(tz=UTC).astimezone().isoformat(timespec='seconds')}
    _atomic_write(folder / 'render.json', json.dumps(info, indent=1).encode('utf-8'))
    ip.push({_RENDER_NAME: name}, interactive=False)
    return folder


def _record_pdf(ip, d):
    """Add the digest `d` of the figure just displayed as PDF to the render's
    figures, after those drawn before it."""
    folder = _render_folder(ip)
    if folder is None:
        return
    n = len(list(folder.glob('figure-*.json'))) + 1
    _atomic_write(folder / f'figure-{n:04d}.json',
                  json.dumps(d, separators=(',', ':')).encode('utf-8'))


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


def _image_bytes(out, kind):
    """The bytes of the image a figure formatter returned. A pair, as the
    double-resolution PNG formatter returns, holds the image first; a string
    is base64 for PNG, JPEG and PDF, and the image's own text for SVG."""
    data = out[0] if isinstance(out, tuple) else out
    if isinstance(data, str):
        return data.encode('utf-8') if kind == 'svg' else base64.b64decode(data)
    return bytes(data)


def _wrap_formatter(registry, kind):
    """Wrap the figure formatter in `registry` so that what it displays has
    its digest filed in the store, and, in a Quarto render, a PDF in the
    render's figures."""
    from matplotlib.figure import Figure
    try:
        current = registry.lookup_by_type(Figure)
    except KeyError:
        return False
    if getattr(current, '_figtools_digest', False):
        return True

    def with_digest(fig):
        d = describe(fig)
        with _keep_layout(fig):
            out = current(fig)
        if out is None:   # a figure with nothing drawn displays nothing
            return out
        remember(d, _image_bytes(out, kind), kind)
        if kind == 'pdf':
            ip = _shell()
            if ip is not None:
                _record_pdf(ip, d)
        return out

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
    """(registry, its kind, its figure formatter) for each image registry that
    holds a figure formatter."""
    from matplotlib.figure import Figure
    found = []
    for mime, kind in _FORMATS.items():
        registry = ip.display_formatter.formatters.get(mime)
        if registry is None:
            continue
        try:
            found.append((registry, kind, registry.lookup_by_type(Figure)))
        except KeyError:
            continue
    return found


def install():
    """File the digest of every figure a notebook kernel displays.

    Wraps the formatters that turn a figure into its PNG, JPEG, SVG or PDF
    for display; each returns the image as it was and files the figure's
    digest in the store under the image's hash. Does nothing, and returns
    False, outside IPython, in a shell with no kernel (terminal IPython,
    which keeps its window backend) or under a backend other than the
    inline one. Resolves the inline backend first when it is still pending:
    its configuration selects the figure formatters, and would replace the
    wrappers if it ran later, at the first figure. In a Quarto render that
    displays figures as PDF, opens the render's folder of figures
    (`_render_folder`), so that a render drawing none leaves an empty one.
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
    found = _figure_formatters(ip)
    if not found:
        warnings.warn('figtools.digest: figures are displayed as none of PNG, JPEG, '
                      'SVG and PDF, so no digest is kept for them', stacklevel=2)
        return False
    for registry, kind, _ in found:
        _wrap_formatter(registry, kind)
    if any(kind == 'pdf' for _, kind, _ in found):
        _render_folder(ip)
    return True


def installed():
    """Whether a figure formatter of the running shell files the digest;
    False outside IPython."""
    ip = _shell()
    if ip is None:
        return False
    return any(getattr(f, '_figtools_digest', False) for _, _, f in _figure_formatters(ip))


def uninstall():
    """Put back the figure formatters `install()` wrapped; return whether any was.

    Only the digest goes: the inline backend `install()` resolved stays.
    Does nothing, and returns False, outside IPython or when no formatter
    files the digest.
    """
    from matplotlib.figure import Figure
    ip = _shell()
    if ip is None:
        return False
    changed = False
    for registry, _, f in _figure_formatters(ip):
        if getattr(f, '_figtools_digest', False):
            registry.for_type(Figure, f._figtools_original)
            changed = True
    return changed


# --- the companion of a saved figure ------------------------------------------

def _plain(v):
    """One value as the companion writes it: a number to six significant
    digits, a missing value as NA, text on one line."""
    if v is None:
        return 'NA'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return 'NA' if math.isnan(v) else f'{v:.6g}'
    return ' '.join(str(v).split())


# The value columns of each kind of series, in the companion's order, with
# the heading each takes.
_LINE_COLUMNS = (('x', 'x'), ('y', 'y'), ('lo', 'lo'), ('hi', 'hi'),
                 ('xlo', 'x lo'), ('xhi', 'x hi'))
_COLUMNS = {'line': _LINE_COLUMNS, 'points': _LINE_COLUMNS, 'errorbar': _LINE_COLUMNS,
            'band': (('x', 'x'), ('lo', 'lo'), ('hi', 'hi')),
            'bars': (('category', 'category'), ('value', 'value'), ('base', 'base')),
            'intervals': (('at', 'at'), ('lo', 'from'), ('hi', 'to'))}


def _series_text(s, j, series):
    bits = [s['kind']]
    if s.get('orientation'):
        bits.append(s['orientation'])
    if s.get('role') == 'guide':
        bits.append('reference curve')
    if s.get('named_by') == 'key':
        bits.append('named by the key')
    if s.get('color'):
        bits.append(s['color'])
    lines = [f'series {j}: {_plain(_name(s, series))} ({", ".join(bits)})']
    if s.get('note'):
        lines.append(f'note: {s["note"]}')
    if s.get('thinned'):
        lines.append(f'thinned: {s["thinned"]}')
    cols = [(key, head) for key, head in _COLUMNS.get(s['kind'], ()) if key in s]
    if cols:
        lines.append('\t'.join(head for _, head in cols))
        lines += ['\t'.join(_plain(s[key][i]) for key, _ in cols)
                  for i in range(len(s[cols[0][0]]))]
    if s.get('segments'):
        lines.append('x0\ty0\tx1\ty1')
        lines += ['\t'.join(_plain(v) for v in seg) for seg in s['segments']]
    return lines


def _panel_text(p, k):
    head = f'panel {k + 1}'
    if p.get('twin_of') is not None:
        head += f', a second axis over panel {p["twin_of"] + 1}'
    lines = [head]
    if p.get('title'):
        lines.append(f'title: {_plain(p["title"])}')
    for a in ('x', 'y'):
        kind = p[f'{a}kind'] + (f', {p[a + "scale"]} scale' if p.get(f'{a}scale') else '')
        label = f'{_plain(p[a + "label"])} ' if p.get(f'{a}label') else ''
        lines.append(f'{a}: {label}({kind})')
    lines.append('')
    series = p.get('series', [])
    for j, s in enumerate(series, start=1):
        lines += _series_text(s, j, series) + ['']

    def named(label):
        return f' "{_plain(label)}"' if label else ''

    def colour(c):
        return f' ({c})' if c else ''

    extra = [f'reference line: {r["axis"]} = {_plain(r["value"])}{named(r.get("label"))}'
             for r in p.get('rules', [])]
    extra += [f'shaded: {s["axis"]} from {_plain(s["from"])} to {_plain(s["to"])}'
              f'{named(s.get("label"))}' for s in p.get('shades', [])]
    extra += [f'point: {_plain(q["text"])} at {_plain(q["x"])}, {_plain(q["y"])}'
              f'{colour(q.get("color"))}' for q in p.get('points', [])]
    extra += [f'note: {_plain(n["text"])}{colour(n.get("color"))}' for n in p.get('notes', [])]
    for im in p.get('images', []):
        span = (f', values from {_plain(im["min"])} to {_plain(im["max"])}'
                if 'min' in im else '')
        extra.append(f'image: grid {"x".join(str(v) for v in im["shape"])}, extent '
                     f'{" ".join(_plain(v) for v in im["extent"])}{span}')
        extra += ['\t'.join(_plain(v) for v in row) for row in im.get('values', [])]
    extra += [f'not digested: {_plain(m)}' for m in p.get('incomplete', [])]
    if extra:
        lines += extra + ['']
    return lines


def companion(name, source, files, d):
    """The text of the companion `save` writes for the figure saved as `name`.

    `source` is where `save` was called, `<file>:<line>`, or None when it is
    not known; `files` the PDFs written, as (file name, width, height) in
    inches; `d` the figure's digest read with no cap, or None when the
    digest is off. The text holds nothing that changes between two runs of
    the same code: values keep six significant digits, and every list keeps
    the figure's own order.
    """
    lines = [f'figure: {name}']
    if source:
        lines.append(f'source: {source}')
    lines += [f'file: {f}, {_plain(round(w, 3))} x {_plain(round(h, 3))} in'
              for f, w, h in files]
    if d is None:
        lines.append('digest: off, so no values are recorded')
        return '\n'.join(lines) + '\n'
    lines += [('digest: every plotted value, one per line, to six significant digits; '
               'tab-separated columns'), '']
    if d.get('title'):
        lines += [f'title: {_plain(d["title"])}', '']
    for k, p in enumerate(d['panels']):
        lines += _panel_text(p, k)
    return '\n'.join(lines).rstrip('\n') + '\n'


def companion_path(path):
    """The companion of `save(fig, path)`: `path` with `.digest.txt` added."""
    path = Path(path)
    return path.with_name(path.name + COMPANION)


_SOURCE_LINE = re.compile(r'^(source: .*):\d+$', re.MULTILINE)
_FILE_LINE = re.compile(r'^file: (.+), \S+ x \S+ in$', re.MULTILINE)


def read_companion(path, errors='strict'):
    """A companion's text, its line ends read as `\\n` whatever they are on
    disk: git on Windows may check text files out with CRLF."""
    return Path(path).read_bytes().decode('utf-8', errors).replace('\r\n', '\n')


def _head(text):
    """A companion's lines before its first blank line: the name, the source,
    the files and the digest line."""
    return text.split('\n\n', 1)[0]


def _without_line_number(text):
    head, sep, body = text.partition('\n\n')
    return _SOURCE_LINE.sub(r'\1:', head) + sep + body


def write_companion(path, text):
    """Write `text` to the companion at `path`, unless the file already holds
    it but for the line number of the call and its line ends; return
    whether it wrote.

    Code moved within its file changes nothing else, so the file, and the
    version control tracking it, stay as they were; the line recorded is
    then where the call was when the figure last changed.
    """
    path = Path(path)
    try:
        old = read_companion(path)
    except FileNotFoundError:
        old = None
    if old is not None and _without_line_number(old) == _without_line_number(text):
        return False
    _atomic_write(path, text.encode('utf-8'))
    return True


def companion_files(text):
    """The PDF file names a companion's text lists."""
    return _FILE_LINE.findall(_head(text.replace('\r\n', '\n')))


def find_companion(figure):
    """The companion of a PDF `save` wrote, or None: `<name>.digest.txt` for
    `<name>.pdf`, and for a variant `<name>_<target>.pdf` the companion of
    `<name>`, found by dropping one `_` part of the file's name at a time.
    A companion counts only when it lists the file."""
    figure = Path(figure)
    stem = figure.stem
    while True:
        candidate = figure.with_name(stem + COMPANION)
        if candidate.is_file() and figure.name in companion_files(
                read_companion(candidate)):
            return candidate
        if '_' not in stem:
            return None
        stem = stem.rsplit('_', 1)[0]


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
    beside the page. An absolute address is not fetched. An embedded image
    is a PNG, JPEG, SVG or PDF by its type; a linked file by its own
    signature, or, for an SVG, which has none, by its extension; anything
    else is no figure this reads.
    """
    if not src:
        return 'Image without a source; nothing digested.'
    if src.startswith('data:'):
        head, _, body = src.partition(',')
        mime = head[len('data:'):].split(';')[0]
        if mime not in _FORMATS:
            return (f'Image embedded as {mime or "untyped data"}, none of PNG, JPEG, SVG '
                    f'and PDF; nothing digested.')
        if ';base64' in head:
            try:
                data = base64.b64decode(body)
            except (ValueError, TypeError):
                return 'Image embedded as data that is not base64; nothing digested.'
        else:
            data = unquote_to_bytes(body)
        return _FORMATS[mime], data
    parts = urlsplit(src)
    if parts.scheme or parts.netloc or src.startswith('/'):
        return f'Image linked by the absolute address {src}, which is not fetched; nothing digested.'
    path = folder / unquote(parts.path)
    if not path.is_file():
        return f'Image file {path} not found; nothing digested.'
    data = path.read_bytes()
    for kind, signature in (('png', b'\x89PNG'), ('jpeg', b'\xff\xd8\xff'), ('pdf', b'%PDF')):
        if data.startswith(signature):
            return kind, data
    if path.suffix.lower() == '.svg':
        return 'svg', data
    return f'Image file {path} holds none of PNG, JPEG, SVG and PDF; nothing digested.'


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
        return f'digest: skipped {f}: {made}'
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

    Path equality ignores case on Windows, and both paths are compared
    resolved, which collapses junctions. One project reached by two routes
    therefore matches itself, including a root recorded under a folder's
    former name that a junction still serves, as Dropbox leaves when it moves
    its folder. A root that no longer holds a `_quarto.yml` belongs to a
    project that moved or was deleted, and its digest is stale rather than
    another's.
    """
    return theirs is not None and Path(theirs).resolve() != Path(ours) and _is_project(theirs)


def _stamp(src, root, output):
    """(source path to print, its sha256, the output's render time)."""
    rel = src.resolve().relative_to(Path(root).resolve()).as_posix() if root else src.name
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    rendered = (datetime.fromtimestamp(Path(output).stat().st_mtime, tz=UTC)
                .astimezone().strftime('%Y-%m-%d %H:%M'))
    return rel, sha, rendered


def extract(html, out, source=None, root=None, cache=None):
    """Write a rendered page's outputs to `out`; return (summary, split).

    `out` is emptied first, so a stale figure from an earlier render cannot
    survive; a folder that holds files and no manifest is refused, since it
    is not one this wrote, and so is one whose manifest names another
    project root than `root` that still exists (`_clear`). Each figure's
    image is read from the page, embedded in it or linked beside it
    (`_image`), and its digest looked up in the store of `cache`
    (`cache_root()` unless given) by the image's hash, which marks the
    entry as read. `split` holds the labels of the figures whose caption
    another output in the same cell took over (`_Page`); a caller driving a
    render is expected to fail on it, not note it.
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
            d = recall(data, cache, touch=True)
            if d is None:
                lines += [f'### {label}' + (f'. {caption}' if caption else ''), '',
                          (f'No digest in the store for this image: the figure was '
                           f'displayed without figtools.setup running, or by figtools '
                           f'0.9 or earlier, or its digest was pruned; see {name}.{ext}.'), '']
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


_RENDER_FOLDER = re.compile(r'(\d+)-\d+')


def _latest_render(renders, sha):
    """(folder, digests in drawing order) of the newest render recorded under
    `renders` from the source whose sha256 is `sha`; (None, []) when there is
    none.

    A render's folder is complete when post-render runs: Quarto runs the
    hook only after the kernel has executed the whole document, and a render
    that fails stops before it. The source's hash keeps a render of another
    version of the document from being taken, as when a document now draws
    no figure as PDF, or Quarto took this render's outputs from its freeze
    or cache without executing: then the newest render of the same source
    is the one those outputs came from. A folder whose figures are not
    numbered 1, 2, ... without a gap is not one this module wrote whole, and
    is passed over.
    """
    best = None
    for folder in sorted(renders.iterdir()) if renders.is_dir() else []:
        m = _RENDER_FOLDER.fullmatch(folder.name)
        info = folder / 'render.json'
        if m is None or not info.is_file():
            continue
        if json.loads(info.read_text(encoding='utf-8')).get('sha256') != sha:
            continue
        names = sorted(p.name for p in folder.glob('figure-*.json'))
        if names != [f'figure-{n:04d}.json' for n in range(1, len(names) + 1)]:
            continue
        if best is None or int(m[1]) > best[0]:
            best = (int(m[1]), folder, names)
    if best is None:
        return None, []
    _, folder, names = best
    return folder, [json.loads((folder / n).read_text(encoding='utf-8')) for n in names]


def extract_pdf(pdf, out, source=None, root=None, renders=None):
    """Write the figures of a document's PDF render to `out`; return a summary.

    The figures are those the kernel recorded, in drawing order, for the
    newest render of the document's present source (`_latest_render`),
    under `renders`, the `_pdf_renders` folder beside `out` unless given;
    the document's other renders are then removed. A PDF render yields its
    figures' digests and nothing else: captions, tables and printed output
    come from the digest of the same document's HTML render.
    """
    pdf, out = Path(pdf), Path(out)
    renders = out.parent / RENDERS if renders is None else Path(renders)
    src = Path(source) if source else source_for(pdf)
    _clear(out, root)
    rel, sha, rendered = _stamp(src, root, pdf)
    chosen, digests = _latest_render(renders, sha)
    lines = [f'# Figures of {rel}, from its PDF render', '',
             (f'Rendered {rendered} from the source with sha256 {sha[:12]}. '
              f'`render-digest check {rel}` says whether the source changed since.'),
             ('A PDF render yields its figures and nothing else, numbered in the order the '
              'document draws them: their captions, the tables and the printed output are '
              'in the digest of the HTML render of the same document, one folder up.'), '']
    if chosen is None:
        lines += [('No figure digests were recorded for this source. The kernel records '
                   'them for figures it displays as PDF, which a PDF render does unless '
                   '`fig-format` says otherwise, once `figtools.setup()` has run.'), '']
    for k, d in enumerate(digests, start=1):
        name = f'figure-{k}'
        lines += markdown(d, name, None, name, None, None)
        _write_csv(out / f'{name}.csv', d)
    (out / 'outputs.md').write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    manifest = {'source': rel, 'sha256': sha, 'pdf': str(pdf), 'rendered': rendered,
                'written': datetime.now(tz=UTC).astimezone().isoformat(timespec='seconds'),
                'project_root': str(Path(root).resolve()) if root is not None else None,
                'render': chosen.name if chosen else None,
                'counts': {'figures': len(digests)}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    if chosen is not None:
        for folder in renders.iterdir():
            if folder != chosen and _RENDER_FOLDER.fullmatch(folder.name):
                shutil.rmtree(folder)
    return f'{len(digests)} figure{"" if len(digests) == 1 else "s"} from the PDF render'


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
    under `cache` (`cache_root()` unless given): a page's through `extract`,
    a PDF's through `extract_pdf`. An output made by no .py,
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
            summary, split = extract(output, out, src, root, cache)
            print(f'digest: {summary} -> {out}')
            if split:
                lost[f] = split
        else:
            summary = extract_pdf(output, out / 'pdf', src, root, out / RENDERS)
            print(f'digest: {summary} -> {out / "pdf"}')
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


def figure_digest(path, cache=None):
    """What `render-digest figure` prints for the file at `path`; None when
    it has no digest.

    A companion is printed as it is, and so is the companion of a PDF
    `save` wrote (`find_companion`). Any other PNG, JPEG, SVG or PDF, such
    as a figure a rendered page holds, gets the digest the store of `cache`
    (`cache_root()` unless given) holds under its hash, as markdown.
    """
    path = Path(path)
    if path.name.endswith(COMPANION):
        return read_companion(path)
    data = path.read_bytes()
    if data.startswith(b'%PDF'):
        found = find_companion(path)
        if found is not None:
            return read_companion(found)
    d = recall(data, cache)
    return None if d is None else '\n'.join(markdown(d, path.stem))


# --- pruning ------------------------------------------------------------------

def _pdf_manifest(folder):
    """Whether `folder` holds the manifest of a PDF render's digest."""
    manifest = Path(folder) / 'manifest.json'
    return manifest.is_file() and 'pdf' in json.loads(manifest.read_text(encoding='utf-8'))


def _gone(root, document):
    """Whether a recorded document no longer exists, or its project's folder
    no longer holds its `_quarto.yml`."""
    return not _is_project(root) or not Path(document).is_file()


def _stale_documents(cache):
    """The digest folders of documents that no longer exist, as
    [(folder, document)], and the recorded PDF renders of such documents
    whose folder holds no manifest, as [(render folder, document)].

    A folder's manifests record the project's folder and the document's
    path in it; a folder whose manifests record no project folder (written
    by `extract` with no root) is left alone, having nothing to go by.
    """
    verdicts = {}
    for project in sorted(cache.iterdir()) if cache.is_dir() else []:
        if not project.is_dir() or project.name == FIGURES:
            continue
        for manifest in sorted(project.rglob('manifest.json')):
            if RENDERS in manifest.parts:
                continue
            info = json.loads(manifest.read_text(encoding='utf-8'))
            root, source = info.get('project_root'), info.get('source')
            if root is None or source is None:
                continue
            folder = manifest.parent
            if 'pdf' in info and folder.name == 'pdf':
                folder = folder.parent
            doc = Path(root, source)
            gone, _ = verdicts.get(folder, (True, doc))
            verdicts[folder] = (gone and _gone(root, doc), doc)
    documents = [(folder, doc) for folder, (gone, doc) in verdicts.items() if gone]
    renders = []
    for project in sorted(cache.iterdir()) if cache.is_dir() else []:
        if not project.is_dir() or project.name == FIGURES:
            continue
        for info in sorted(project.rglob(f'{RENDERS}/*/render.json')):
            folder = info.parent.parent.parent
            if folder in verdicts:
                continue
            recorded = json.loads(info.read_text(encoding='utf-8'))
            if _gone(recorded['project_root'], recorded['document']):
                renders.append((info.parent, Path(recorded['document'])))
    return documents, renders


def _orphan_companions(project):
    """The companions under the folder `project` none of whose PDFs exists
    beside them. Folders whose name starts with a dot (`.git`, `.venv`), and
    links into other folders, are not searched."""
    found = []
    for here, dirs, names in os.walk(project):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__'
                         and not os.path.islink(os.path.join(here, d))
                         and not _is_junction(os.path.join(here, d)))
        for name in sorted(names):
            if not name.endswith(COMPANION):
                continue
            path = Path(here, name)
            text = read_companion(path, errors='replace')
            files = companion_files(text)
            if text.startswith('figure: ') and files and not any(
                    (path.parent / f).exists() for f in files):
                found.append(path)
    return found


def _is_junction(path):
    return getattr(os.path, 'isjunction', lambda _: False)(path)


def prune(project=None, cache=None, now=None):
    """What `render-digest prune` would remove, as a dict of lists:

    - 'documents': (digest folder, document) for each document that no
      longer exists, or whose project's folder no longer holds its
      `_quarto.yml`, from the paths its manifests record;
    - 'renders': (render folder, document) for each recorded PDF render of
      such a document that no manifest covers;
    - 'figures': the store's entries neither written nor read in the last
      `STALE_DAYS` days, `now` being the time to count from (the present
      unless given);
    - 'companions': with a `project` folder given, the companions under it
      none of whose PDFs exists beside them.
    """
    cache = cache_root() if cache is None else Path(cache)
    now = time.time() if now is None else now
    documents, renders = _stale_documents(cache)
    store = cache / FIGURES
    figures = sorted(p for p in store.glob('*.json')
                     if now - p.stat().st_mtime > STALE_DAYS * 86400) if store.is_dir() else []
    return {'documents': documents, 'renders': renders, 'figures': figures,
            'companions': _orphan_companions(project) if project is not None else []}


def _remove_document(folder, cache):
    """Remove a document's digest, its PDF render's and its recorded renders
    from `folder`, keeping the folders of other documents nested in it, then
    every folder left empty up to the cache."""
    for p in folder.iterdir():
        if p.is_file():
            p.unlink()
    if (folder / 'pdf').is_dir() and _pdf_manifest(folder / 'pdf'):
        shutil.rmtree(folder / 'pdf')
    if (folder / RENDERS).is_dir():
        shutil.rmtree(folder / RENDERS)
    _remove_empty(folder, cache)


def _remove_empty(folder, cache):
    while folder != cache and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _prune_command(project, yes, cache=None):
    """`render-digest prune`: list, ask, remove, and say what went."""
    cache = cache_root() if cache is None else Path(cache)
    found = prune(project, cache)
    if not any(found.values()):
        print('render-digest prune: nothing to remove')
        return 0
    if found['documents']:
        print('Digests of documents that no longer exist:')
        print('\n'.join(f'  {folder}  ({doc})' for folder, doc in found['documents']))
    if found['renders']:
        print('Recorded PDF renders of documents that no longer exist:')
        print('\n'.join(f'  {folder}  ({doc})' for folder, doc in found['renders']))
    if found['figures']:
        print(f'Store entries neither written nor read in {STALE_DAYS} days: '
              f'{len(found["figures"])} in {cache / FIGURES}')
    if found['companions']:
        print('Companions none of whose figure files exists:')
        print('\n'.join(f'  {p}' for p in found['companions']))
    if not yes:
        try:
            answer = input('Remove them? [y/N] ')
        except EOFError:   # no terminal to answer from
            answer = ''
        if answer.strip().lower() not in ('y', 'yes'):
            print('render-digest prune: nothing removed; --yes removes without asking')
            return 0
    for folder, _ in found['documents']:
        _remove_document(folder, cache)
        print(f'removed {folder}')
    for folder, _ in found['renders']:
        shutil.rmtree(folder)
        _remove_empty(folder.parent, cache)
        print(f'removed {folder}')
    for p in found['figures']:
        p.unlink()
    if found['figures']:
        print(f'removed {len(found["figures"])} store entries from {cache / FIGURES}')
    for p in found['companions']:
        p.unlink()
        print(f'removed {p}')
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
    g = sub.add_parser('figure', help='print a saved figure\'s companion, or the digest the '
                                      'store holds for an image')
    g.add_argument('images', type=Path, nargs='+')
    p = sub.add_parser('prune', help='list stale digests and companions, and remove them '
                                     'after asking')
    p.add_argument('project', type=Path, nargs='?',
                   help='a project folder, also searched for companions whose figure is gone')
    p.add_argument('--yes', action='store_true', help='remove without asking')
    a = ap.parse_args(argv)

    if a.cmd == 'post-render':
        post_render()
        return 0
    # A FIGTOOLS_DIGEST_DIR that names no usable folder leaves no digest to
    # read either. Checked on its own so that any other ValueError still
    # raises.
    try:
        cache_root()
    except ValueError as e:
        print(f'render-digest {a.cmd}: {e}', file=sys.stderr)
        return 2
    if a.cmd in ('where', 'check', 'extract'):
        try:
            return _locate(a)
        except FileNotFoundError as e:
            # A document outside any Quarto project, or a file that is not
            # there: no digest to read, which is check's 2. The traceback's
            # exit code, 1, would read as "the source changed" to a gate.
            print(f'render-digest {a.cmd}: {e}', file=sys.stderr)
            return 2
    if a.cmd == 'prune':
        if a.project is not None and not a.project.is_dir():
            print(f'render-digest prune: {a.project} is not a folder', file=sys.stderr)
            return 2
        return _prune_command(a.project, a.yes)
    missing = 0
    for image in a.images:
        try:
            text = figure_digest(image)
        except FileNotFoundError:
            print(f'render-digest figure: {image}: no such file', file=sys.stderr)
            missing += 1
            continue
        if text is None:
            print(f'render-digest figure: {image}: no digest. No companion beside it lists '
                  f'it (save writes one beside its PDFs), and the store holds none under '
                  f'the hash of its bytes ({cache_root() / FIGURES})', file=sys.stderr)
            missing += 1
            continue
        print(text.rstrip('\n'))
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
