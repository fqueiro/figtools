"""Tests for the figure digest: what it reads off a figure, how a displayed
figure's digest reaches the store and a saved figure's its companion, and
what the page extractor writes out.

A digest that silently dropped a series, mislabelled a bar, or lost its
figure's caption would still leave a well-formed markdown file, so each
piece is checked against a figure whose content is known.
"""

import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from IPython.core.formatters import (
    JPEGFormatter,
    PDFFormatter,
    PNGFormatter,
    SVGFormatter,
)
from IPython.core.pylabtools import print_figure, retina_figure

import figtools as ft
from figtools import digest


@pytest.fixture(autouse=True)
def style():
    ft.setup()
    yield
    plt.close('all')


def panel(fig):
    return digest.describe(fig)['panels'][0]


def test_lines_rules_and_shades_are_read_with_their_labels():
    fig, ax = plt.subplots()
    years = list(range(1990, 1995))
    ax.plot(years, [1.0, 1.1, 1.2, 1.15, 1.3], label='Portugal')
    ax.plot(years, [1.0, 0.9, 0.95, 1.0, 1.05], label='North')
    ft.refline(ax, 1, 'parity')
    ft.shade(ax, 1991, 1992, 'fragile')
    ft.label_lines(ax)
    p = panel(fig)
    assert [(s['kind'], s['label']) for s in p['series']] == [('line', 'Portugal'), ('line', 'North')]
    assert p['series'][0]['x'] == years and p['series'][0]['y'][-1] == 1.3
    assert p['rules'] == [{'axis': 'y', 'value': 1, 'label': 'parity'}]
    assert p['shades'] == [{'axis': 'x', 'from': 1991, 'to': 1992, 'label': 'fragile'}]
    assert 'points' not in p and 'notes' not in p   # line labels are not reported as annotations


def test_bars_read_categories_and_values_both_ways():
    fig, (a, b) = plt.subplots(1, 2)
    a.bar(['x', 'y', 'z'], [3, 1, 2], label='counts')
    b.barh(['p', 'q'], [0.5, 1.5])
    d = digest.describe(fig)
    s = d['panels'][0]['series'][0]
    assert (s['kind'], s['orientation'], s['label']) == ('bars', 'vertical', 'counts')
    assert s['category'] == ['x', 'y', 'z'] and s['value'] == [3, 1, 2]
    assert d['panels'][0]['xkind'] == 'category'
    t = d['panels'][1]['series'][0]
    assert t['orientation'] == 'horizontal' and t['category'] == ['p', 'q'] and t['value'] == [0.5, 1.5]


def test_a_line_against_category_names_reads_back_the_names():
    fig, ax = plt.subplots()
    ax.plot([0.2, 0.5, 0.8], ['Portugal', 'Spain', 'Italy'], label='gap')
    s = panel(fig)['series'][0]
    assert (s['kind'], s['x'], s['y']) == ('line', [0.2, 0.5, 0.8], ['Portugal', 'Spain', 'Italy'])


def test_stacked_bars_carry_their_base():
    fig, ax = plt.subplots()
    ax.bar([1, 2], [1, 2])
    ax.bar([1, 2], [3, 4], bottom=[1, 2])
    s = panel(fig)['series']
    assert 'base' not in s[0] and s[1]['base'] == [1, 2] and s[1]['value'] == [3, 4]


def test_dotplot_reads_positions_intervals_and_the_category_labels():
    fig, ax = plt.subplots()
    ft.dotplot(ax, ['a', 'b', 'c'], [0.2, -0.1, 0.5], ci=([0.1, -0.3, 0.3], [0.3, 0.1, 0.7]))
    p = panel(fig)
    pts = next(s for s in p['series'] if s['kind'] == 'points')
    assert pts['y'] == ['b', 'a', 'c'] and pts['x'] == [-0.1, 0.2, 0.5]   # rows sorted by value
    iv = next(s for s in p['series'] if s['kind'] == 'intervals')
    assert iv['orientation'] == 'horizontal'
    assert iv['at'] == ['b', 'a', 'c'] and iv['lo'] == [-0.3, 0.1, 0.3] and iv['hi'] == [0.1, 0.3, 0.7]


def test_dotplot_stems_are_furniture_and_its_table_names_rows_by_category():
    fig, ax = plt.subplots()
    ft.dotplot(ax, ['a', 'b'], [0.2, -0.1])
    d = digest.describe(fig)
    assert [s['kind'] for s in d['panels'][0]['series']] == ['points']
    text = '\n'.join(digest.markdown(d, 'fig-d'))
    assert '| y | unlabelled points 1 |' in text and '| b | -0.1 |' in text
    assert '**Panel 1**.' in text   # bare axes are not described


def test_pointrange_is_a_marker_series_with_vertical_intervals():
    fig, ax = plt.subplots()
    ft.pointrange(ax, [1, 2, 3], [0.1, 0.2, 0.3], [0, 0.1, 0.2], [0.2, 0.3, 0.4], label='beta')
    p = panel(fig)
    assert [s['kind'] for s in p['series']] == ['points', 'intervals']
    assert p['series'][0]['label'] == 'beta'
    assert p['series'][1]['orientation'] == 'vertical' and p['series'][1]['hi'] == [0.2, 0.3, 0.4]


def test_band_takes_the_name_of_its_line_and_errorbars_keep_their_bounds():
    fig, ax = plt.subplots()
    x = [1, 2, 3]
    ax.plot(x, [1, 2, 3], label='fit')
    ft.band(ax, x, [0.5, 1.5, 2.5], [1.5, 2.5, 3.5])
    ax.errorbar([1, 2], [5, 6], yerr=[0.5, 1], label='obs', fmt='o')
    p = panel(fig)
    band = next(s for s in p['series'] if s['kind'] == 'band')
    assert band['label'] == 'band: fit'
    assert band['x'] == x and band['lo'] == [0.5, 1.5, 2.5] and band['hi'] == [1.5, 2.5, 3.5]
    eb = next(s for s in p['series'] if s['kind'] == 'errorbar')
    assert eb['label'] == 'obs' and eb['lo'] == [4.5, 5] and eb['hi'] == [5.5, 7]
    assert not any(s['kind'] == 'points' for s in p['series'])   # the errorbar's marker line only once


def test_errorbars_without_their_points_are_read_from_the_bars():
    """`fmt='none'` draws the bars and no data line, and `bar` draws its
    error bars that way: each bar's position and range are what is left."""
    fig, (a, b, c) = plt.subplots(1, 3)
    a.errorbar([1, 2], [5, 6], yerr=[0.5, 1], fmt='none', label='range')
    b.errorbar([5, 6], [0, 1], xerr=[[1, 0.5], [2, 1]], fmt='none')   # asymmetric
    c.errorbar([1, 2], [5, 6], xerr=0.5, yerr=1, fmt='none')
    d = digest.describe(fig)
    ya, xb, both = (p['series'] for p in d['panels'])
    assert [s['kind'] for s in ya + xb + both] == ['errorbar'] * 3
    ya, xb, both = ya[0], xb[0], both[0]
    assert (ya['label'], ya['x'], ya['lo'], ya['hi']) == ('range', [1, 2], [4.5, 5], [5.5, 7])
    assert 'y' not in ya and ya['note'] == 'error bars drawn without their points'
    assert (xb['y'], xb['xlo'], xb['xhi']) == ([0, 1], [4, 5.5], [7, 7]) and 'x' not in xb
    # the vertical bar stands at the point's x and the horizontal one at its y
    assert (both['x'], both['y'], both['lo'], both['xhi']) == ([1, 2], [5, 6], [4, 5], [1.5, 2.5])

    text = '\n'.join(digest.markdown(d, 'fig-e'))
    assert '| x | lo | hi |' in text and '| 2 | 5 | 7 |' in text
    assert '| y | x lo | x hi |' in text and '| 0 | 4 | 7 |' in text
    assert '*error bars drawn without their points*' in text
    rows = digest.csv_rows(d)
    assert (1, 'range', 'errorbar', 2, None, 5, 7, '') in rows
    assert (2, '', 'errorbar x', None, 1, 5.5, 7, '') in rows
    assert not any(r[0] == 2 and r[2] == 'errorbar' for r in rows)   # no point to report
    assert (3, '', 'errorbar x', 1, 5, 0.5, 1.5, '') in rows

    fig, ax = plt.subplots()
    ax.bar(['a', 'b'], [3, 4], yerr=[0.5, 1])
    s = panel(fig)['series']
    assert [x['kind'] for x in s] == ['errorbar', 'bars']
    assert (s[0]['x'], s[0]['lo'], s[0]['hi']) == (['a', 'b'], [2.5, 3], [3.5, 5])


def test_scatter_labelled_points_and_notes():
    fig, ax = plt.subplots()
    ax.scatter([1, 2, 3], [3, 1, 2], label='obs')
    ft.label_points(ax, [1, 2], [3, 1], ['A', 'B'])
    ft.label_scatter(ax, [4, 5], [4, 5], ['C', 'D'], note=True)
    p = panel(fig)
    assert [s['kind'] for s in p['series']] == ['points'] and p['series'][0]['x'] == [1, 2, 3]
    assert [(q['text'], q['x'], q['y']) for q in p['points']] == [
        ('A', 1, 3), ('B', 2, 1), ('C', 4, 4), ('D', 5, 5)]
    assert p['notes'][0]['text'].startswith('N = 2')


def test_key_entries_name_unlabelled_series_by_colour():
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2], color=ft.PALETTE[0])
    ax.plot([1, 2], [2, 1], color=ft.PALETTE[1])
    ft.key(ax, [('first', ft.PALETTE[0]), ('second', ft.PALETTE[1])])
    p = panel(fig)
    assert [s['label'] for s in p['series']] == ['first', 'second']
    assert all(s['named_by'] == 'key' for s in p['series'])


def test_a_panel_letter_names_no_series():
    """Only a key names a series by colour. A panel letter in the colour of
    an unlabelled line, written as an annotation, as text or as anchored
    text, is a note and leaves the line unnamed."""
    from matplotlib.offsetbox import AnchoredText
    fig, (a, b, c) = plt.subplots(1, 3)
    for ax in (a, b, c):
        ax.plot([1, 2], [1, 2], color='black')
    a.annotate('(a)', xy=(0.02, 0.95), xycoords='axes fraction', color='black')
    b.text(0.02, 0.95, '(b)', transform=b.transAxes, color='black')
    c.add_artist(AnchoredText('(c)', loc='upper left', prop={'color': 'black'}))
    for p, letter in zip(digest.describe(fig)['panels'], 'abc', strict=True):
        s = p['series'][0]
        assert (s['label'], s.get('named_by')) == (None, None), letter
        assert p['notes'] == [{'text': f'({letter})', 'color': '#000000'}]


def test_text_inside_offset_boxes_is_read_line_by_line():
    """A key whose words differ in colour is packed from offset boxes, since
    `key` colours whole lines. Each of its lines is a note, and a line in
    one colour names the unlabelled series drawn in it. Anchored text is a
    note too, and an annotation box pinned to a data point names the point."""
    from matplotlib.offsetbox import (
        AnchoredOffsetbox,
        AnchoredText,
        AnnotationBbox,
        HPacker,
        TextArea,
        VPacker,
    )

    def row(parts):
        return HPacker(children=[TextArea(t, textprops={'color': c}) for t, c in parts],
                       pad=0, sep=0, align='baseline')
    fig, ax = plt.subplots()
    ax.scatter([1, 2], [1, 2], color=ft.PALETTE[0])
    rows = [row([('●  ', ft.PALETTE[0]), ('observed', ft.PALETTE[0])]),
            row([('without the ', ft.GREY['text']), ('workers', ft.PALETTE[1]),
                 (' term', ft.GREY['text'])])]
    ax.add_artist(AnchoredOffsetbox(loc='upper left', frameon=False,
                                    child=VPacker(children=rows, align='left', pad=0, sep=4)))
    ax.add_artist(AnchoredText('source: INE', loc='lower right'))
    ax.add_artist(AnnotationBbox(TextArea('peak'), (2, 2), xybox=(20, 0),
                                 boxcoords='offset points'))
    d = digest.describe(fig)
    p = d['panels'][0]
    grey, first, second = (digest._color(c) for c in (ft.GREY['text'], *ft.PALETTE[:2]))
    assert p['notes'][:2] == [
        {'text': '● observed', 'color': first},
        {'text': 'without the workers term',
         'parts': [{'text': 'without the', 'color': grey}, {'text': 'workers', 'color': second},
                   {'text': 'term', 'color': grey}]}]
    assert p['notes'][2]['text'] == 'source: INE' and len(p['notes']) == 3
    assert [(q['text'], q['x'], q['y']) for q in p['points']] == [('peak', 2, 2)]
    assert (p['series'][0]['label'], p['series'][0]['named_by']) == ('● observed', 'key')
    text = '\n'.join(digest.markdown(d, 'fig-k'))
    assert 'Notes: ● observed; without the workers term; source: INE.' in text
    assert 'Labelled points: peak (2, 2).' in text


def test_legend_entries_are_the_series_labels_and_are_not_repeated():
    """A legend shows the labels its series already carry, so its entries
    add nothing, only its title. Entries given to unlabelled series
    (`ax.legend(['A', 'B'])`) name them through their handles' colours."""
    fig, (a, b) = plt.subplots(1, 2)
    a.plot([1, 2], [1, 2], label='Portugal')
    a.bar([1, 2], [1, 2], label='firms')
    a.legend(title='Region')
    b.plot([1, 2], [1, 2])
    b.plot([1, 2], [2, 1])
    b.legend(['A', 'B'])
    pa, pb = digest.describe(fig)['panels']
    assert sorted(s['label'] for s in pa['series']) == ['Portugal', 'firms']
    assert pa['notes'] == [{'text': 'Region'}]
    assert [(s['label'], s.get('named_by')) for s in pb['series']] == [('A', 'key'), ('B', 'key')]
    assert [n['text'] for n in pb['notes']] == ['A', 'B']


def test_date_axes_read_as_iso_dates_and_twins_are_marked():
    fig, ax = plt.subplots()
    days = [dt.date(2000, 1, 1), dt.date(2001, 7, 1)]
    ax.plot(days, [1, 2], label='s')
    tw = ax.twinx()
    tw.plot(days, [10, 20], label='t')
    d = digest.describe(fig)
    assert d['panels'][0]['xkind'] == 'date'
    assert d['panels'][0]['series'][0]['x'] == ['2000-01-01', '2001-07-01']
    assert d['panels'][1]['twin_of'] == 0 and d['panels'][1]['series'][0]['label'] == 't'


def test_small_image_grids_are_digested_in_full_and_large_ones_flagged():
    fig, (a, b) = plt.subplots(1, 2)
    a.imshow(np.arange(4).reshape(2, 2))
    b.imshow(np.zeros((50, 50)))
    d = digest.describe(fig)
    assert d['panels'][0]['images'][0]['values'] == [[0, 1], [2, 3]]
    assert 'values' not in d['panels'][1]['images'][0] and d['panels'][1]['incomplete']


def test_log_scales_and_reference_curves_are_marked():
    fig, ax = plt.subplots()
    ax.plot([1, 10, 100], [1, 2, 3], label='s')
    ax.set_xscale('log')
    ft.refcurve(ax, [1, 100], [1, 3])
    d = digest.describe(fig)
    p = d['panels'][0]
    assert p['xscale'] == 'log'
    assert [s.get('role') for s in p['series']] == [None, 'guide']   # the JSON keeps its value
    text = '\n'.join(digest.markdown(d, 'fig-r'))
    assert '(line, reference curve):' in text and ', guide' not in text


def test_long_series_are_thinned_and_say_so():
    fig, ax = plt.subplots()
    n = digest.SERIES_CAP * 3
    ax.plot(np.arange(n), np.arange(n) * 2.0, label='long')
    s = panel(fig)['series'][0]
    assert len(s['x']) == digest.SERIES_CAP and s['x'][0] == 0 and s['x'][-1] == n - 1
    assert s['thinned'].startswith(f'{n} points')


def png_text_keys(data):
    """The keys of a PNG's text chunks (tEXt, zTXt, iTXt)."""
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    keys, i = [], 8
    while i + 8 <= len(data):
        (n,) = struct.unpack('>I', data[i:i + 4])
        tag = data[i + 4:i + 8]
        if tag in (b'tEXt', b'zTXt', b'iTXt'):
            keys.append(data[i + 8:i + 8 + n].partition(b'\x00')[0].decode('latin-1'))
        i += 12 + n
    return keys


# A figure formatter of each kind as the inline backend registers it, and
# the IPython registry it goes in.
DISPLAYED = {
    'png': ('image/png', lambda fig: print_figure(fig, fmt='png', base64=True)),
    'retina': ('image/png', lambda fig: retina_figure(fig, base64=True)),
    'jpeg': ('image/jpeg', lambda fig: print_figure(fig, fmt='jpg', base64=True)),
    'svg': ('image/svg+xml', lambda fig: print_figure(fig, fmt='svg')),
    'pdf': ('application/pdf', lambda fig: print_figure(fig, fmt='pdf', base64=True)),
}
REGISTRIES = {'image/png': PNGFormatter, 'image/jpeg': JPEGFormatter,
              'image/svg+xml': SVGFormatter, 'application/pdf': PDFFormatter}


@pytest.mark.parametrize('fmt', DISPLAYED)
def test_a_displayed_figure_is_returned_as_drawn_and_its_digest_filed(fmt, private_store):
    """The formatter returns the very object the inline backend's formatter
    made, so the page gets the image as matplotlib drew it, and the digest
    goes into the store under the hash of the image's bytes."""
    from matplotlib.figure import Figure
    mime, draw = DISPLAYED[fmt]
    made = []
    registry = REGISTRIES[mime]()
    registry.for_type(Figure, lambda fig: made.append(draw(fig)) or made[-1])
    assert digest._wrap_formatter(registry, digest._FORMATS[mime])
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    out = registry.lookup_by_type(Figure)(fig)
    assert out is made[-1]
    data = digest._image_bytes(out, digest._FORMATS[mime])
    assert b'figtools' not in data
    if mime == 'image/png':
        assert png_text_keys(data) == ['Software']   # matplotlib's own, and nothing else
    entry = private_store / digest.FIGURES / f'{hashlib.sha256(data).hexdigest()}.json'
    assert entry.is_file()
    assert digest.recall(data)['panels'][0]['series'][0]['label'] == 's'
    assert digest.recall(data + b' ') is None


def test_the_store_marks_an_entry_read_when_asked(private_store):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    entry = digest.remember(digest.describe(fig), b'image', 'png')
    old = time.time() - 100 * 86400
    os.utime(entry, (old, old))
    assert digest.recall(b'image') is not None and entry.stat().st_mtime == pytest.approx(old)
    digest.recall(b'image', touch=True)
    assert entry.stat().st_mtime > old + 99 * 86400


def test_setup_arms_the_notebook_formatters_once():
    # In its own interpreter: enabling the inline backend is process-wide.
    # Formats are selected before setup, as a Quarto render does, and the
    # shell stands in for a kernel's, which carries the kernel it serves.
    # The store is the test's own, inherited through FIGTOOLS_DIGEST_DIR.
    code = textwrap.dedent('''
        import base64, json
        import matplotlib
        matplotlib.use('module://matplotlib_inline.backend_inline')
        from IPython.core.interactiveshell import InteractiveShell
        ip = InteractiveShell.instance()
        ip.kernel = object()
        from matplotlib_inline.backend_inline import set_matplotlib_formats
        set_matplotlib_formats('png', 'jpeg', 'svg', 'pdf')
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        import figtools as ft
        from figtools import digest
        mimes = ('image/png', 'image/jpeg', 'image/svg+xml', 'application/pdf')
        fmts = ip.display_formatter.formatters
        plain = [fmts[m].lookup_by_type(Figure) for m in mimes]
        ft.setup()
        first = [fmts[m].lookup_by_type(Figure) for m in mimes]
        assert all(getattr(f, '_figtools_digest', False) for f in first)
        assert digest.installed()
        ft.setup()
        assert [fmts[m].lookup_by_type(Figure) for m in mimes] == first
        fig, ax = plt.subplots()
        ax.plot([1, 2], [3, 4], label='s')
        labels = []
        for mime, f in zip(mimes, first):
            data = digest._image_bytes(f(fig), digest._FORMATS[mime])
            assert b'figtools' not in data
            labels.append(digest.recall(data)['panels'][0]['series'][0]['label'])
        assert digest.uninstall() and not digest.installed()
        assert [fmts[m].lookup_by_type(Figure) for m in mimes] == plain
        print(json.dumps(labels))
        ''')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == ['s'] * 4


def fake_shell(kernel=True):
    """An IPython shell with real image formatter registries, each holding
    the formatter the inline backend registers, a kernel or none, a
    namespace, and an `enable_matplotlib` that records its calls."""
    from functools import partial
    from types import SimpleNamespace

    from matplotlib.figure import Figure

    # The shape the inline backend registers: print_figure with keywords.
    formats = {'image/png': partial(print_figure, fmt='png', base64=True),
               'image/jpeg': partial(print_figure, fmt='jpg', base64=True),
               'image/svg+xml': partial(print_figure, fmt='svg'),
               'application/pdf': partial(print_figure, fmt='pdf', base64=True)}
    fmts = {mime: REGISTRIES[mime]() for mime in formats}
    for mime, f in formats.items():
        fmts[mime].for_type(Figure, f)
    calls = []
    ns = {}
    ip = SimpleNamespace(display_formatter=SimpleNamespace(formatters=fmts),
                         enable_matplotlib=calls.append, user_ns=ns,
                         push=lambda names, interactive=True: ns.update(names))
    if kernel:
        ip.kernel = object()
    return ip, calls, formats


def pending_backend(monkeypatch, ip):
    """The state of a fresh shell: no backend chosen yet, and `ip` running."""
    import IPython.core.getipython
    monkeypatch.setattr(IPython.core.getipython, 'get_ipython', lambda: ip)
    monkeypatch.setattr(matplotlib, 'get_backend', lambda *a, **k: None)


def inline_resolved(monkeypatch):
    """The inline backend's configuration has run, as it has in a notebook."""
    import matplotlib_inline.backend_inline as inline
    monkeypatch.setattr(inline.configure_inline_support, 'current_backend', 'inline',
                        raising=False)


def test_install_leaves_a_shell_without_a_kernel_alone(monkeypatch):
    """Terminal IPython has no kernel and no backend chosen yet; arming the
    digest there would switch it to the inline backend, and `plt.show()`
    would open no window."""
    from matplotlib.figure import Figure
    ip, calls, plain = fake_shell(kernel=False)
    pending_backend(monkeypatch, ip)
    assert digest.install() is False
    assert calls == [] and not digest.installed()
    fmts = ip.display_formatter.formatters
    assert {m: fmts[m].lookup_by_type(Figure) for m in plain} == plain


def test_uninstall_puts_back_what_install_wrapped(monkeypatch):
    from matplotlib.figure import Figure
    ip, calls, plain = fake_shell()
    pending_backend(monkeypatch, ip)
    inline_resolved(monkeypatch)
    fmts = ip.display_formatter.formatters
    assert not digest.installed() and digest.uninstall() is False
    assert digest.install() is True and calls == []
    assert all(getattr(fmts[m].lookup_by_type(Figure), '_figtools_digest', False) for m in plain)
    assert ip.user_ns == {}   # no Quarto render, so no render is recorded
    assert digest.uninstall() is True
    assert {m: fmts[m].lookup_by_type(Figure) for m in plain} == plain
    assert not digest.installed() and digest.uninstall() is False
    # with only one of them wrapped, the digest is still armed
    digest._wrap_formatter(fmts['application/pdf'], 'pdf')
    assert digest.installed()


def test_install_warns_when_no_image_format_is_displayed(monkeypatch):
    from matplotlib.figure import Figure
    ip, _, plain = fake_shell()
    for mime in plain:
        ip.display_formatter.formatters[mime].pop(Figure)
    pending_backend(monkeypatch, ip)
    inline_resolved(monkeypatch)
    with pytest.warns(UserWarning, match='none of PNG, JPEG, SVG and PDF'):
        assert digest.install() is False


def test_installed_and_uninstall_are_safe_outside_ipython(monkeypatch):
    import IPython.core.getipython
    monkeypatch.setattr(IPython.core.getipython, 'get_ipython', lambda: None)
    assert digest.installed() is False and digest.uninstall() is False and digest.install() is False


@pytest.fixture
def pdf_render(tmp_path, monkeypatch):
    """A Quarto render of `code/doc.py` in a project, displaying figures as
    PDF only, as Quarto's setup cell leaves the formatters for `fig-format:
    pdf`: (shell, project, document)."""
    from matplotlib.figure import Figure
    project = tmp_path / 'proj'
    (project / 'code').mkdir(parents=True)
    (project / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    doc = project / 'code' / 'doc.py'
    doc.write_text('# %%\nimport figtools\n', encoding='utf-8')
    monkeypatch.delenv('QUARTO_EXECUTE_INFO', raising=False)
    monkeypatch.setenv('QUARTO_DOCUMENT_FILE', 'doc.py')
    monkeypatch.setenv('QUARTO_DOCUMENT_PATH', 'code')   # relative, as under the daemon
    monkeypatch.chdir(project / 'code')
    ip, _, _ = fake_shell()
    for mime in ('image/png', 'image/jpeg', 'image/svg+xml'):
        ip.display_formatter.formatters[mime].pop(Figure)
    pending_backend(monkeypatch, ip)
    inline_resolved(monkeypatch)
    return ip, project, doc


def show_pdf(ip, label):
    from matplotlib.figure import Figure
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2], label=label)
    ip.display_formatter.formatters['application/pdf'].lookup_by_type(Figure)(fig)
    plt.close(fig)


def test_a_pdf_render_records_its_figures_in_the_order_drawn(pdf_render):
    """Quarto deletes a PDF render's figure files, so the kernel records the
    digests in drawing order, in a folder of the render's own. The next
    render, in the same kernel as under Quarto's daemon, starts after
    `%reset` has cleared the namespace, and gets a folder of its own;
    post-render takes the newest and drops the rest."""
    ip, project, doc = pdf_render
    renders = digest.digest_dir(project, doc) / digest.RENDERS
    assert digest.install()
    first, = renders.iterdir()   # opened by setup, before any figure
    info = json.loads((first / 'render.json').read_text(encoding='utf-8'))
    assert info['sha256'] == hashlib.sha256(doc.read_bytes()).hexdigest()
    assert info['document'] == str(doc.resolve())
    show_pdf(ip, 'a')
    assert digest.install()   # a second setup in the same render changes nothing
    show_pdf(ip, 'b')
    assert sorted(p.name for p in first.iterdir()) == [
        'figure-0001.json', 'figure-0002.json', 'render.json']
    second_figure = json.loads((first / 'figure-0002.json').read_text(encoding='utf-8'))
    assert second_figure['panels'][0]['series'][0]['label'] == 'b'
    ip.user_ns.clear()   # Quarto's %reset, at the end of one render and the start of the next
    assert digest.install()
    show_pdf(ip, 'c')
    second, = (p for p in renders.iterdir() if p != first)
    assert [p.name for p in second.glob('figure-*.json')] == ['figure-0001.json']
    pdf = project / 'code' / 'doc.pdf'
    pdf.write_bytes(b'%PDF-1.5\n')
    out = digest.digest_dir(project, doc)
    assert digest.extract_pdf(pdf, out / 'pdf', doc, project) == '1 figure from the PDF render'
    assert [p.name for p in renders.iterdir()] == [second.name]
    text = (out / 'pdf' / 'outputs.md').read_text(encoding='utf-8')
    assert '### figure-1' in text and '| x | c |' in text and '| x | a |' not in text


def test_a_render_outside_a_project_records_nothing(pdf_render, private_store):
    ip, project, _ = pdf_render
    (project / '_quarto.yml').unlink()
    assert digest.install()
    show_pdf(ip, 'a')
    assert sorted(p.name for p in private_store.iterdir()) == [digest.FIGURES]


def write_render(renders, name, sha, figures):
    folder = renders / name
    folder.mkdir(parents=True)
    (folder / 'render.json').write_text(json.dumps({'sha256': sha}), encoding='utf-8')
    for n in figures:
        d = {'v': 1, 'panels': [{'xkind': 'number', 'ykind': 'number', 'series': [
            {'kind': 'line', 'label': f'{name}/{n}', 'color': None, 'x': [1, 2], 'y': [1, 2]}]}]}
        (folder / f'figure-{n:04d}.json').write_text(json.dumps(d), encoding='utf-8')


def test_the_newest_render_of_the_present_source_is_taken(tmp_path):
    """A render of another version of the source is never taken: a source
    whose figures went could otherwise get those of an older render. One
    whose figures are not numbered without a gap was not written whole."""
    renders = tmp_path / digest.RENDERS
    write_render(renders, '100-1', 'aaa', [1, 2])
    write_render(renders, '200-1', 'aaa', [1])
    write_render(renders, '300-1', 'bbb', [1])
    write_render(renders, '400-1', 'aaa', [1, 3])
    folder, found = digest._latest_render(renders, 'aaa')
    assert folder.name == '200-1'
    assert [d['panels'][0]['series'][0]['label'] for d in found] == ['200-1/1']
    assert digest._latest_render(renders, 'ccc') == (None, [])
    assert digest._latest_render(tmp_path / 'none', 'aaa') == (None, [])


def test_a_pdf_render_with_no_recorded_figures_says_so(tmp_path):
    (tmp_path / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = tmp_path / 'doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    pdf = tmp_path / 'doc.pdf'
    pdf.write_bytes(b'%PDF-1.5\n')
    cache = tmp_path / 'cache'
    out = digest.digest_dir(tmp_path, src, cache)
    write_render(out / digest.RENDERS, '100-1', 'another source', [1])
    assert digest.extract_pdf(pdf, out / 'pdf', src, tmp_path) == '0 figures from the PDF render'
    text = (out / 'pdf' / 'outputs.md').read_text(encoding='utf-8')
    assert text.startswith('# Figures of doc.qmd, from its PDF render')
    assert 'No figure digests were recorded for this source' in text
    assert (out / digest.RENDERS / '100-1').is_dir()   # kept: nothing was taken in its place
    assert digest.check(src, cache=cache) == 0   # the PDF render's manifest stands in
    html = tmp_path / 'doc.html'
    html.write_text('<main><h2>S</h2></main>', encoding='utf-8')
    digest.extract(html, out, source=src, root=tmp_path, cache=cache)
    assert (out / 'outputs.md').exists() and (out / 'pdf' / 'outputs.md').exists()
    assert (out / digest.RENDERS / '100-1').is_dir()   # a page's digest keeps the renders


def test_the_companion_states_each_panel_series_rule_and_note():
    fig, (a, b) = plt.subplots(1, 2)
    a.plot([2000, 2001], [1.0, 2.5], label='north')
    ft.refline(a, 1.5, 'mid')
    ft.shade(a, 2000.2, 2000.4, 'slump')
    a.set_ylabel('Share\twith a tab')
    a.set_title('Left')
    b.bar(['x', 'y'], [3, 4], label='counts')
    b.annotate('peak', xy=(1, 4))
    b.text(0.5, 0.9, 'N = 2', transform=b.transAxes)
    files = [('fig.pdf', 5.9, 4.425), ('fig_slides.pdf', 4.1, 3.075)]
    text = digest.companion('fig', '../code/doc.py:12', files, digest.describe(fig, cap=None))
    assert text.splitlines()[:5] == [
        'figure: fig', 'source: ../code/doc.py:12', 'file: fig.pdf, 5.9 x 4.425 in',
        'file: fig_slides.pdf, 4.1 x 3.075 in',
        ('digest: every plotted value, one per line, to six significant digits; '
         'tab-separated columns')]
    assert '\npanel 1\ntitle: Left\nx: (number)\ny: Share with a tab (number)\n' in text
    assert '\nseries 1: north (line, #0077bb)\nx\ty\n2000\t1\n2001\t2.5\n' in text
    assert '\nreference line: y = 1.5 "mid"\nshaded: x from 2000.2 to 2000.4 "slump"\n' in text
    assert '\npanel 2\nx: (category)\ny: (number)\n' in text
    assert '\nseries 1: counts (bars, vertical, #0077bb)\ncategory\tvalue\nx\t3\ny\t4\n' in text
    assert '\npoint: peak at y, 4 (#000000)\nnote: N = 2 (#000000)\n' in text
    assert text.endswith('\n') and not text.endswith('\n\n') and '\r' not in text
    assert digest.companion_files(text) == ['fig.pdf', 'fig_slides.pdf']


def test_the_digest_is_the_same_drawn_or_not_and_at_any_size():
    """`save` reads the digest before drawing, and leaves undrawn a figure
    whose PDFs are current; a notebook may have drawn the figure first. The
    placement that runs at each draw moves and wraps labels, which the
    digest does not record, so the companion is the same either way."""
    def build():
        fig, (a, b) = plt.subplots(1, 2, figsize=(8, 4))
        yr = np.arange(2000, 2016)
        a.plot(yr, np.sin(yr / 3), label='a label long enough to be wrapped')
        a.plot(yr, np.cos(yr / 3), label='short')
        ft.refline(a, 0.5, 'a rule')
        ft.label_lines(a)
        b.plot([1, 2, 3], [1, 2, 3], color=ft.PALETTE[0])
        b.plot([1, 2, 3], [3, 2, 1], color=ft.PALETTE[1])
        ft.key(b, [('up', ft.PALETTE[0]), ('down', ft.PALETTE[1])])
        ft.label_points(b, [1, 3], [1, 1], ['p', 'q'])
        return fig

    def read(fig):
        d = digest.describe(fig, cap=None)
        d.pop('size_in')
        return d

    undrawn = read(build())
    fig = build()
    fig.canvas.draw()
    assert read(fig) == undrawn
    fig.set_size_inches(4.1, 2.0)
    fig.canvas.draw()
    assert read(fig) == undrawn


def test_a_companion_keeps_every_point_the_store_thins():
    fig, ax = plt.subplots()
    n = digest.SERIES_CAP + 7
    ax.plot(np.arange(n), np.arange(n) * 0.5, label='long')
    assert len(digest.describe(fig)['panels'][0]['series'][0]['x']) == digest.SERIES_CAP
    text = digest.companion('fig', None, [('fig.pdf', 5.9, 4.0)], digest.describe(fig, cap=None))
    rows = text.split('series 1: long (line, #0077bb)\nx\ty\n', 1)[1].splitlines()
    assert len(rows) == n and rows[1] == '1\t0.5' and rows[-1] == f'{n - 1}\t{(n - 1) / 2:g}'
    assert 'thinned' not in text


def test_a_companion_without_the_digest_keeps_its_source_and_files():
    text = digest.companion('fig', 'make.py:3', [('fig.pdf', 5.9, 4.0)], None)
    assert text == ('figure: fig\nsource: make.py:3\nfile: fig.pdf, 5.9 x 4 in\n'
                    'digest: off, so no values are recorded\n')


def test_a_companion_differing_only_in_the_line_of_the_call_is_left_as_it_is(tmp_path):
    path = tmp_path / 'fig.digest.txt'
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    text = digest.companion('fig', 'make.py:3', [('fig.pdf', 5.9, 4.0)],
                            digest.describe(fig, cap=None))
    assert digest.write_companion(path, text)
    before = path.stat().st_mtime_ns, path.read_bytes()
    assert not digest.write_companion(path, text)
    assert not digest.write_companion(path, text.replace('make.py:3', 'make.py:30'))
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before
    assert digest.write_companion(path, text.replace('make.py:3', 'draw.py:3'))
    assert digest.write_companion(path, text.replace('3\t4', '3\t5'))
    assert digest.write_companion(path, text.replace('source: make.py:3\n', ''))
    assert 'source:' not in path.read_text(encoding='utf-8')
    assert b'\r' not in path.read_bytes()
    # git on Windows may check the file out with CRLF line ends: the same text
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    assert not digest.write_companion(path, text.replace('source: make.py:3\n', ''))
    assert [p.name for p in tmp_path.iterdir()] == ['fig.digest.txt']   # no temporary file left


def test_markdown_merges_series_on_a_shared_x_and_thins_large_tables():
    fig, ax = plt.subplots()
    x = list(range(300))
    ax.plot(x, x, label='a')
    ax.plot(x, [2 * v for v in x], label='b')
    ax.plot([0, 1], [5, 6], label='c')
    ft.refline(ax, 1, 'one')
    ax.annotate('peak', xy=(2, 0.123456))
    text = '\n'.join(digest.markdown(digest.describe(fig), 'fig-t', 'A caption.', 'fig-t'))
    assert text.startswith('### fig-t. A caption.')
    assert '**Panel 1**.' in text and 'x: x' not in text
    assert '| x | a | b |' in text and 'rows thinned to' in text
    assert '| x | c |' in text
    assert 'Reference lines: y = 1 "one".' in text
    assert 'Labelled points: peak (2, 0.1235).' in text
    assert 'fig-t.csv' in text


def test_csv_rows_cover_series_rules_and_bars():
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    ft.refline(ax, 3.5, 'mid')
    ax.bar([1], [2])
    rows = digest.csv_rows(digest.describe(fig))
    assert rows[0][:4] == ('panel', 'series', 'kind', 'x')
    assert {r[2] for r in rows[1:]} == {'line', 'rule y', 'bars vertical'}


PAGE = '''
<html><body><main class="content">
<header><div class="quarto-title"><h1 class="title">The title</h1></div></header>
<section><h2 class="anchored">First section</h2>
<div class="cell"><div class="cell-output cell-output-stdout"><pre>hello 42</pre></div></div>
<div id="fig-double" class="cell quarto-float quarto-figure"><figure class="quarto-float-fig figure">
<div class="cell-output cell-output-display"><img src="data:image/png;base64,{png}" class="figure-img"></div>
<figcaption class="quarto-float-caption">Figure&nbsp;1: Doubling.</figcaption></figure></div>
<div class="cell"><div class="cell-output cell-output-display">
<table class="dataframe"><thead><tr><th></th><th>a</th></tr><tr><th>idx</th><th></th></tr></thead>
<tbody><tr><th>r1</th><td>1.5</td></tr></tbody></table></div></div>
<div class="cell"><div class="cell-output cell-output-display"><img src="data:image/png;base64,{plain}"></div></div>
</section></main><nav id="TOC"><h2>Table of contents</h2></nav></body></html>
'''


def test_extract_writes_digests_tables_and_text_from_a_rendered_page(tmp_path):
    """Each image the page embeds finds its digest in the store by the hash
    of its bytes; one displayed without figtools has none there."""
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [2, 4, 6], label='double')
    png = print_figure(fig, fmt='png', base64=False)
    digest.remember(digest.describe(fig), png, 'png')
    ax.plot([1, 2, 3], [3, 3, 3], label='flat')
    plain = print_figure(fig, fmt='png', base64=True)
    src = tmp_path / 'doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = tmp_path / 'doc.html'
    html.write_text(PAGE.format(png=base64.b64encode(png).decode(), plain=plain),
                    encoding='utf-8')
    out = tmp_path / 'cache' / 'proj' / 'doc'
    summary, split = digest.extract(html, out, source=src, root=tmp_path)
    assert summary == '2 figures, 1 table, 1 text block'
    assert split == []   # the text block here is its own output, not a subfloat
    text = (out / 'outputs.md').read_text(encoding='utf-8')
    assert text.startswith('# Outputs of doc.qmd\n\nTitle: The title\n')
    assert '## First section' in text and 'hello 42' in text
    assert 'Table of contents' not in text
    assert '### fig-double. Figure 1: Doubling.' in text and '| x | double |' in text
    assert '| idx | a |\n|---|---|\n| r1 | 1.5 |' in text
    assert '### fig-2\n\nNo digest in the store for this image' in text
    assert (out / 'fig-double.png').read_bytes() == png and (out / 'fig-double.csv').exists()
    assert (out / 'fig-2.png').exists() and not (out / 'fig-2.csv').exists()
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['sha256'] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert [f['digest'] for f in manifest['figures']] == [True, False]


LINKED = '''
<html><body><main class="content">
<div id="fig-linked" class="cell quarto-float quarto-figure"><figure class="quarto-float-fig figure">
<div class="cell-output cell-output-display"><img src="my%20doc_files/figure-html/fig-linked-output-1.png" class="figure-img"></div>
<figcaption class="quarto-float-caption">Figure&nbsp;1: Linked.</figcaption></figure></div>
<div class="cell"><div class="cell-output cell-output-display"><img src="my%20doc_files/figure-html/gone.png"></div></div>
<div class="cell"><div class="cell-output cell-output-display"><img src="https://example.org/fig.png"></div></div>
<div id="fig-svg" class="cell"><div class="cell-output cell-output-display"><img src="my%20doc_files/figure-html/fig-svg-output-1.svg"></div></div>
<div id="fig-jpeg" class="cell"><div class="cell-output cell-output-display"><img src="my%20doc_files/figure-html/fig-jpeg-output-1.jpeg"></div></div>
<div id="fig-inline" class="cell"><div class="cell-output cell-output-display"><img src="data:image/svg+xml;base64,{svg}"></div></div>
</main></body></html>
'''


def test_extract_reads_images_linked_beside_the_page(tmp_path):
    """Unless a page embeds its resources, Quarto links each figure's file,
    written to `<document>_files/figure-html/` beside the page, inside the
    output folder when there is one. A PNG, a JPEG and an SVG each find
    their digest by their bytes, the SVG's being its text as UTF-8."""
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [2, 4, 6], label='double')
    d = digest.describe(fig)
    png = print_figure(fig, fmt='png', base64=False)
    jpeg = print_figure(fig, fmt='jpg', base64=False)
    svg = print_figure(fig, fmt='svg').encode('utf-8')
    for data, kind in ((png, 'png'), (jpeg, 'jpeg'), (svg, 'svg')):
        digest.remember(d, data, kind)
    site = tmp_path / '_site'
    figures = site / 'my doc_files' / 'figure-html'
    figures.mkdir(parents=True)
    (figures / 'fig-linked-output-1.png').write_bytes(png)
    (figures / 'fig-svg-output-1.svg').write_bytes(svg)
    (figures / 'fig-jpeg-output-1.jpeg').write_bytes(jpeg)
    src = tmp_path / 'my doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = site / 'my doc.html'
    html.write_text(LINKED.format(svg=base64.b64encode(svg).decode()), encoding='utf-8')
    out = tmp_path / 'cache' / 'proj' / 'my doc'
    summary, _ = digest.extract(html, out, source=src, root=tmp_path)
    assert summary == '6 figures, 0 tables, 0 text blocks'
    text = (out / 'outputs.md').read_text(encoding='utf-8')
    assert '### fig-linked. Figure 1: Linked.' in text and '| x | double |' in text
    assert (out / 'fig-linked.png').read_bytes() == png and (out / 'fig-linked.csv').exists()
    gone = figures.resolve() / 'gone.png'
    assert f'### fig-2\n\nImage file {gone} not found; nothing digested.' in text
    assert ('### fig-3\n\nImage linked by the absolute address https://example.org/fig.png, '
            'which is not fetched; nothing digested.') in text
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['figures'] == [
        {'label': label, 'digest': True, 'files': [f'{label}.{ext}', f'{label}.csv']}
        for label, ext in (('fig-linked', 'png'), ('fig-svg', 'svg'), ('fig-jpeg', 'jpeg'),
                           ('fig-inline', 'svg'))]
    assert (out / 'fig-inline.svg').read_bytes() == svg


def test_extract_refuses_to_clear_a_folder_it_did_not_write(tmp_path):
    src = tmp_path / 'doc.qmd'
    src.write_text('x', encoding='utf-8')
    html = tmp_path / 'doc.html'
    html.write_text('<main></main>', encoding='utf-8')
    out = tmp_path / 'precious'
    out.mkdir()
    (out / 'keep.txt').write_text('mine', encoding='utf-8')
    with pytest.raises(FileExistsError):
        digest.extract(html, out, source=src)
    assert (out / 'keep.txt').exists()


def test_check_tells_fresh_from_stale_from_missing(tmp_path, capsys):
    (tmp_path / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = tmp_path / 'code' / 'doc.qmd'
    src.parent.mkdir()
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = src.with_suffix('.html')
    html.write_text('<main><h2>S</h2></main>', encoding='utf-8')
    cache = tmp_path / 'cache'
    assert digest.check(src, cache=cache) == 2
    out = digest.digest_dir(tmp_path, src, cache)
    assert out == cache / tmp_path.name / 'code' / 'doc'
    digest.extract(html, out, source=src, root=tmp_path)
    assert digest.check(src, cache=cache) == 0
    src.write_text('---\ntitle: T2\n---\n', encoding='utf-8')
    assert digest.check(src, cache=cache) == 1
    assert 'stale' in capsys.readouterr().out

def _page(subfloats):
    """A Quarto figure float whose subfloats hold the given bodies."""
    inner = ''.join(
        f'<div id="fig-x-{k}" class="cell-output cell-output-display '
        f'quarto-float">'
        f'<figure class="quarto-float quarto-subfloat-fig figure">{b}'
        f'<figcaption>({chr(96 + k)}) sub</figcaption></figure></div>'
        for k, b in enumerate(subfloats, start=1))
    return ('<main><div id="fig-x" class="cell quarto-float">'
            '<figure class="quarto-float quarto-float-fig figure">'
            f'{inner}<figcaption>outer caption</figcaption>'
            '</figure></div></main>')


IMG = '<img src="data:image/png;base64,zz">'


def test_a_figure_that_lost_its_caption_to_another_output_is_named(tmp_path):
    # The bug: a repr or a table shares the captioned cell, so Quarto makes
    # it a subfloat and the caption lands on it instead of on the figure.
    for body in ('<pre><code>[Text(0, 0, \'a\')]</code></pre>',
                 '<table><tr><td>1</td></tr></table>'):
        page = digest._Page()
        page.feed(_page([body, IMG]))
        page.close()
        assert page.split == ['fig-x'], body

    # A deliberate multi-panel figure: every subfloat is an image, so the
    # guard stays quiet however many there are.
    for n in (2, 3):
        page = digest._Page()
        page.feed(_page([IMG] * n))
        page.close()
        assert page.split == []

    # Nor does it fire on a table or printed output outside a figure.
    page = digest._Page()
    page.feed('<main><div class="cell-output cell-output-display">'
              '<pre><code>text</code></pre></div>'
              '<div class="cell-output"><table><tr><td>1</td></tr></table>'
              '</div></main>')
    page.close()
    assert page.split == []


def test_post_render_fails_on_a_figure_that_lost_its_caption(tmp_path,
                                                             monkeypatch):
    (tmp_path / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = tmp_path / 'doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = tmp_path / 'doc.html'
    html.write_text(_page(['<pre><code>[Text(0, 0, \'a\')]</code></pre>',
                           IMG]), encoding='utf-8')
    quarto_env(monkeypatch, tmp_path, None, ['doc.html'])
    with pytest.raises(SystemExit) as e:
        digest.post_render(cache=tmp_path / 'cache')
    assert 'fig-x' in str(e.value) and str(e.value).endswith('plt.show().')
    # the advice names the second outputs that still occur
    assert 'ft.key' not in digest.SPLIT_ADVICE and 'ft.dotplot' not in digest.SPLIT_ADVICE
    # the page is digested before the failure, so the evidence is on disk
    out = digest.digest_dir(tmp_path, src, tmp_path / 'cache')
    assert json.loads((out / 'manifest.json').read_text())['split'] == ['fig-x']


def quarto_env(monkeypatch, project, output_dir, files):
    """What Quarto hands a post-render script, run from the project folder:
    the outputs relative to the project, in the platform's separators."""
    monkeypatch.chdir(project)
    monkeypatch.setenv('QUARTO_PROJECT_DIR', str(project))
    monkeypatch.setenv('QUARTO_PROJECT_OUTPUT_DIR', str(project / output_dir) if output_dir
                       else str(project))
    monkeypatch.setenv('QUARTO_PROJECT_OUTPUT_FILES', '\n'.join(
        str(Path(output_dir, f) if output_dir else Path(f)) for f in files))


@pytest.mark.parametrize('output_dir', [None, '_out'])
def test_post_render_finds_each_source_through_the_output_folder(tmp_path, monkeypatch,
                                                                 output_dir):
    """With `output-dir` (and in every website and book) Quarto writes
    `_out/sub/other.html` for `sub/other.py`; without one the page sits
    beside its source."""
    (tmp_path / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    (tmp_path / 'doc.qmd').write_text('---\ntitle: T\n---\n', encoding='utf-8')
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'other.py').write_text('# %%\n', encoding='utf-8')
    site = tmp_path / output_dir if output_dir else tmp_path
    (site / 'sub').mkdir(parents=True, exist_ok=True)
    (site / 'doc.html').write_text('<main><h2>S</h2></main>', encoding='utf-8')
    (site / 'sub' / 'other.html').write_text('<main><h2>O</h2></main>', encoding='utf-8')
    (site / 'doc.pdf').write_bytes(b'%PDF-1.5\n')   # the same document rendered to PDF
    (site / 'search.json').write_text('{}', encoding='utf-8')
    quarto_env(monkeypatch, tmp_path, output_dir,
               ['doc.html', 'doc.pdf', str(Path('sub') / 'other.html'), 'search.json'])
    cache = tmp_path / 'cache'
    monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', str(cache))   # read when post_render runs
    sha = hashlib.sha256((tmp_path / 'doc.qmd').read_bytes()).hexdigest()
    write_render(cache / tmp_path.name / 'doc' / digest.RENDERS, '100-1', sha, [1, 2])
    digest.post_render()
    base = cache / tmp_path.name
    doc = json.loads((base / 'doc' / 'manifest.json').read_text(encoding='utf-8'))
    other = json.loads((base / 'sub' / 'other' / 'manifest.json').read_text(encoding='utf-8'))
    assert (doc['source'], other['source']) == ('doc.qmd', 'sub/other.py')
    assert doc['project_root'] == other['project_root'] == str(tmp_path.resolve())
    pdf = json.loads((base / 'doc' / 'pdf' / 'manifest.json').read_text(encoding='utf-8'))
    assert (pdf['source'], pdf['counts']) == ('doc.qmd', {'figures': 2})
    assert not (base / 'search').exists()


def test_post_render_skips_pages_and_pdfs_no_document_made(tmp_path, monkeypatch, capsys):
    """A website's about.md runs no figtools code, and a book rendered to PDF
    writes one file for all its chapters, under the book's title. Each is
    skipped with a line saying why, and the render's other pages are
    digested; a page with no source at all still fails it (below)."""
    (tmp_path / '_quarto.yml').write_text('project:\n  type: website\n', encoding='utf-8')
    (tmp_path / 'doc.qmd').write_text('---\ntitle: T\n---\n', encoding='utf-8')
    (tmp_path / 'about.md').write_text('# About\n', encoding='utf-8')
    site = tmp_path / '_site'
    site.mkdir()
    for name in ('doc', 'about'):
        (site / f'{name}.html').write_text('<main><h2>S</h2></main>', encoding='utf-8')
    for name in ('My-Book', 'about'):
        (site / f'{name}.pdf').write_bytes(b'%PDF-1.5\n')
    quarto_env(monkeypatch, tmp_path, '_site', ['doc.html', 'about.html', 'My-Book.pdf',
                                                'about.pdf'])
    cache = tmp_path / 'cache'
    digest.post_render(cache=cache)
    out = capsys.readouterr().out.splitlines()
    page, book, pdf = (Path('_site', f) for f in ('about.html', 'My-Book.pdf', 'about.pdf'))
    assert f'digest: skipped {page}: its source about.md runs no figtools code' in out
    assert f"digest: skipped {book}: no single document made it, as with a book's PDF" in out
    assert f'digest: skipped {pdf}: its source about.md runs no figtools code' in out
    assert [p.name for p in (cache / tmp_path.name).iterdir()] == ['doc']


def test_post_render_names_an_output_without_one_source(tmp_path, monkeypatch):
    (tmp_path / 'doc.qmd').write_text('x', encoding='utf-8')
    (tmp_path / 'doc.py').write_text('x', encoding='utf-8')
    (tmp_path / '_out').mkdir()
    for name in ('doc', 'lost'):
        (tmp_path / '_out' / f'{name}.html').write_text('<main></main>', encoding='utf-8')
    quarto_env(monkeypatch, tmp_path, '_out', ['lost.html'])
    with pytest.raises(FileNotFoundError, match=r'lost.*found none'):
        digest.post_render(cache=tmp_path / 'cache')
    quarto_env(monkeypatch, tmp_path, '_out', ['doc.html'])
    with pytest.raises(FileNotFoundError, match=r'found doc\.py, doc\.qmd'):
        digest.post_render(cache=tmp_path / 'cache')
    monkeypatch.delenv('QUARTO_PROJECT_OUTPUT_DIR')
    with pytest.raises(SystemExit, match='QUARTO_PROJECT_OUTPUT_DIR'):
        digest.post_render(cache=tmp_path / 'cache')
    with pytest.raises(ValueError, match='together'):
        digest.source_for(tmp_path / '_out' / 'doc.html', tmp_path / '_out')


def test_the_cache_root_follows_the_environment_at_call_time(tmp_path, monkeypatch):
    src = tmp_path / 'proj' / 'code' / 'doc.qmd'
    monkeypatch.delenv('FIGTOOLS_DIGEST_DIR', raising=False)
    assert digest.digest_dir(tmp_path / 'proj', src) == digest.CACHE_ROOT / 'proj' / 'code' / 'doc'
    monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', str(tmp_path / 'digests'))
    assert digest.digest_dir(tmp_path / 'proj', src) == tmp_path / 'digests' / 'proj' / 'code' / 'doc'
    for bad in ('', 'relative/digests'):
        monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', bad)
        with pytest.raises(ValueError, match='FIGTOOLS_DIGEST_DIR must name an absolute'):
            digest.digest_dir(tmp_path / 'proj', src)


def test_projects_sharing_a_folder_name_do_not_overwrite_each_other(tmp_path, capsys):
    cache = tmp_path / 'cache'
    docs = []
    for parent in ('a', 'b'):
        root = tmp_path / parent / 'proj'
        root.mkdir(parents=True)
        (root / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
        src = root / 'doc.qmd'
        src.write_text(f'---\ntitle: {parent}\n---\n', encoding='utf-8')
        src.with_suffix('.html').write_text('<main><h2>S</h2></main>', encoding='utf-8')
        docs.append((root, src))
    (ra, sa), (rb, sb) = docs
    out = digest.digest_dir(ra, sa, cache)
    assert out == digest.digest_dir(rb, sb, cache)   # the clash: one folder for both
    digest.extract(sa.with_suffix('.html'), out, source=sa, root=ra)
    assert json.loads((out / 'manifest.json').read_text())['project_root'] == str(ra.resolve())
    with pytest.raises(FileExistsError) as e:
        digest.extract(sb.with_suffix('.html'), out, source=sb, root=rb)
    assert str(ra.resolve()) in str(e.value) and str(rb.resolve()) in str(e.value)
    assert 'FIGTOOLS_DIGEST_DIR' in str(e.value)
    assert digest.check(sa, cache=cache) == 0
    assert digest.check(sb, cache=cache) == 2   # a's digest is not b's
    assert 'shares this project' in capsys.readouterr().out
    digest.extract(sa.with_suffix('.html'), out, source=sa, root=ra)   # its own folder clears


def quarto_project(root, title):
    """A Quarto project at `root` holding doc.qmd and its rendered page."""
    root.mkdir(parents=True)
    (root / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = root / 'doc.qmd'
    src.write_text(f'---\ntitle: {title}\n---\n', encoding='utf-8')
    src.with_suffix('.html').write_text('<main><h2>S</h2></main>', encoding='utf-8')
    return src


def test_the_digest_of_a_project_that_moved_is_its_own(tmp_path):
    """A project moved to another parent folder keeps its folder name, so its
    digest stays where it was, recording the old path. Nothing is left
    there to protect: the document reads as fresh and re-renders."""
    cache = tmp_path / 'cache'
    sa = quarto_project(tmp_path / 'a' / 'proj', 'T')
    out = digest.digest_dir(sa.parent, sa, cache)
    digest.extract(sa.with_suffix('.html'), out, source=sa, root=sa.parent)
    (tmp_path / 'b').mkdir()
    moved = (tmp_path / 'a' / 'proj').rename(tmp_path / 'b' / 'proj')
    src = moved / 'doc.qmd'
    assert digest.digest_dir(moved, src, cache) == out
    assert digest.check(src, cache=cache) == 0
    digest.extract(src.with_suffix('.html'), out, source=src, root=moved)
    assert json.loads((out / 'manifest.json').read_text())['project_root'] == str(moved.resolve())


def test_a_project_recorded_under_a_junction_at_its_former_path_is_itself(tmp_path):
    """Dropbox moves its folder when the team is renamed and leaves a junction
    at the former name, so the recorded root still holds a _quarto.yml.
    Resolved, it is the project itself, and its digest clears."""
    _winapi = pytest.importorskip('_winapi')
    cache = tmp_path / 'cache'
    sa = quarto_project(tmp_path / 'old' / 'proj', 'T')
    out = digest.digest_dir(sa.parent, sa, cache)
    digest.extract(sa.with_suffix('.html'), out, source=sa, root=sa.parent)
    moved = (tmp_path / 'old').rename(tmp_path / 'new') / 'proj'
    _winapi.CreateJunction(str(tmp_path / 'new'), str(tmp_path / 'old'))
    src = moved / 'doc.qmd'
    assert digest.check(src, cache=cache) == 0
    digest.extract(src.with_suffix('.html'), out, source=src, root=moved)
    assert json.loads((out / 'manifest.json').read_text())['project_root'] == str(moved.resolve())


def test_the_digest_of_a_deleted_project_gives_way_to_its_namesake(tmp_path):
    """While another project of the same folder name exists, its digest is
    not this one's; once its folder holds no _quarto.yml, the digest is
    stale and the next render replaces it."""
    cache = tmp_path / 'cache'
    sa = quarto_project(tmp_path / 'a' / 'proj', 'a')
    sb = quarto_project(tmp_path / 'b' / 'proj', 'b')
    ra, rb = sa.parent, sb.parent
    out = digest.digest_dir(ra, sa, cache)
    digest.extract(sa.with_suffix('.html'), out, source=sa, root=ra)
    assert digest.check(sb, cache=cache) == 2
    with pytest.raises(FileExistsError):
        digest.extract(sb.with_suffix('.html'), out, source=sb, root=rb)
    (ra / '_quarto.yml').unlink()
    assert digest.check(sb, cache=cache) == 1   # a's digest, read as b's stale one
    digest.extract(sb.with_suffix('.html'), out, source=sb, root=rb)
    assert json.loads((out / 'manifest.json').read_text())['project_root'] == str(rb.resolve())
    assert digest.check(sb, cache=cache) == 0


def test_commands_on_a_document_outside_any_project_exit_2(tmp_path, capsys):
    """Outside a Quarto project there is no digest, which is check's 2; the
    exit code of a traceback, 1, would read as a changed source to a gate."""
    src = tmp_path / 'doc.qmd'
    src.write_text('x', encoding='utf-8')
    html = src.with_suffix('.html')
    html.write_text('<main></main>', encoding='utf-8')
    for argv in (['check', str(src)], ['where', str(src)], ['extract', str(html)]):
        assert digest.main(argv) == 2, argv
        err = capsys.readouterr().err
        assert err.startswith(f'render-digest {argv[0]}: ') and 'no _quarto.yml' in err
        assert err.count('\n') == 1


def test_commands_with_an_unusable_cache_folder_exit_2(tmp_path, capsys, monkeypatch):
    """A relative or empty FIGTOOLS_DIGEST_DIR leaves no digest to read,
    which is check's 2 as well, not a traceback's 1."""
    root = tmp_path / 'proj'
    root.mkdir()
    (root / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = root / 'doc.qmd'
    src.write_text('x', encoding='utf-8')
    for value in ('rel/dir', ''):
        monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', value)
        for argv in (['check', str(src)], ['where', str(src)], ['figure', str(src)],
                     ['prune']):
            assert digest.main(argv) == 2, (value, argv)
            err = capsys.readouterr().err
            assert err.startswith(f'render-digest {argv[0]}: ')
            assert 'FIGTOOLS_DIGEST_DIR' in err and err.count('\n') == 1


def test_figure_command_reads_a_companion_or_the_store(tmp_path, capsys):
    """A PDF `save` wrote, and each variant of it, leads to its companion;
    any other image to the digest the store holds under its hash."""
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    ft.label_lines(ax)
    ft.save(fig, tmp_path / 'my_fig')
    for name in ('my_fig.pdf', 'my_fig_slides.pdf', 'my_fig.digest.txt'):
        assert digest.main(['figure', str(tmp_path / name)]) == 0, name
        out = capsys.readouterr().out
        assert out.startswith('figure: my_fig\nsource: ') and '\n1\t3\n2\t4\n' in out
    png = print_figure(fig, fmt='png', base64=False)
    (tmp_path / 'shown.png').write_bytes(png)
    assert digest.main(['figure', str(tmp_path / 'shown.png')]) == 1
    assert 'shown.png: no digest' in capsys.readouterr().err
    digest.remember(digest.describe(fig), png, 'png')
    assert digest.main(['figure', str(tmp_path / 'shown.png')]) == 0
    assert capsys.readouterr().out.startswith('### shown\n')
    (tmp_path / 'my_fig.digest.txt').unlink()
    assert digest.main(['figure', str(tmp_path / 'my_fig.pdf'),
                        str(tmp_path / 'missing.pdf')]) == 1
    err = capsys.readouterr().err
    assert 'my_fig.pdf: no digest' in err and 'missing.pdf: no such file' in err


def test_a_variant_finds_only_a_companion_that_lists_it(tmp_path):
    """`a_b_slides.pdf` belongs to `a_b`; `a.digest.txt`, for a figure saved
    as `a`, does not list it and is passed over."""
    for name in ('a', 'a_b'):
        (tmp_path / f'{name}{digest.COMPANION}').write_text(digest.companion(
            name, None, [(f'{name}.pdf', 5.9, 4), (f'{name}_slides.pdf', 4.1, 3)], None))
    assert digest.find_companion(tmp_path / 'a_b_slides.pdf') == tmp_path / 'a_b.digest.txt'
    assert digest.find_companion(tmp_path / 'a_slides.pdf') == tmp_path / 'a.digest.txt'
    assert digest.find_companion(tmp_path / 'a_poster.pdf') is None
    assert digest.find_companion(tmp_path / 'c.pdf') is None


def test_prune_lists_what_went_stale_and_removes_it_when_told(tmp_path, monkeypatch, capsys,
                                                               private_store):
    live = quarto_project(tmp_path / 'live' / 'proj', 'L')
    gone = quarto_project(tmp_path / 'gone' / 'old', 'G')
    nested = gone.parent / 'doc' / 'part.qmd'   # its digest folder sits inside doc's
    nested.parent.mkdir()
    nested.write_text('---\ntitle: P\n---\n', encoding='utf-8')
    nested.with_suffix('.html').write_text('<main><h2>S</h2></main>', encoding='utf-8')
    folders = {}
    for src in (live, gone, nested):
        root = src.parent if src is not nested else gone.parent
        folders[src] = digest.digest_dir(root, src)
        digest.extract(src.with_suffix('.html'), folders[src], source=src, root=root)
    write_render(folders[gone] / digest.RENDERS, '100-1', 'sha', [1])
    shutil.rmtree(tmp_path / 'gone')
    d = {'v': 1, 'panels': []}
    fresh, old = digest.remember(d, b'fresh', 'png'), digest.remember(d, b'old', 'png')
    then = time.time() - (digest.STALE_DAYS + 1) * 86400
    os.utime(old, (then, then))
    figures = live.parent / 'figures'
    figures.mkdir()
    for name in ('kept', 'lost'):
        (figures / f'{name}{digest.COMPANION}').write_text(digest.companion(
            name, None, [(f'{name}.pdf', 5.9, 4), (f'{name}_slides.pdf', 4.1, 3)], None))
    (figures / 'kept_slides.pdf').write_bytes(b'%PDF-1.4\n')   # the paper file went; this stays
    (figures / 'notes.digest.txt').write_text('not a companion\n')
    found = digest.prune(live.parent)
    assert sorted(doc for _, doc in found['documents']) == sorted(
        [Path(gone.parent.resolve(), 'doc.qmd'), Path(gone.parent.resolve(), 'doc/part.qmd')])
    assert found['renders'] == [] and found['figures'] == [old]
    assert found['companions'] == [figures / 'lost.digest.txt']
    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    assert digest.main(['prune', str(live.parent)]) == 0
    out = capsys.readouterr().out
    assert 'nothing removed' in out and str(figures / 'lost.digest.txt') in out
    assert old.exists() and folders[gone].exists()
    assert digest.main(['prune', str(live.parent), '--yes']) == 0
    out = capsys.readouterr().out
    assert f'removed {folders[gone]}' in out and 'removed 1 store entries' in out
    assert not old.exists() and fresh.exists()
    assert not (figures / 'lost.digest.txt').exists() and (figures / 'kept.digest.txt').exists()
    assert not (private_store / 'old').exists()   # the gone project's folder, left empty
    assert (folders[live] / 'manifest.json').exists()
    assert digest.prune(live.parent) == {'documents': [], 'renders': [], 'figures': [],
                                         'companions': []}
    assert digest.main(['prune']) == 0
    assert 'nothing to remove' in capsys.readouterr().out


def test_prune_finds_the_recorded_renders_of_a_document_that_went(tmp_path, private_store):
    """A document rendered to PDF whose post-render never ran leaves only
    its recorded renders."""
    folder = private_store / 'proj' / 'doc' / digest.RENDERS / '100-1'
    folder.mkdir(parents=True)
    (folder / 'render.json').write_text(json.dumps({
        'document': str(tmp_path / 'proj' / 'doc.py'), 'project_root': str(tmp_path / 'proj'),
        'sha256': 'x'}), encoding='utf-8')
    found = digest.prune()
    assert found['renders'] == [(folder, tmp_path / 'proj' / 'doc.py')]
    assert found['documents'] == []
