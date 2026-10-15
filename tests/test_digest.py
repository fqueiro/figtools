"""Tests for the figure digest: what it reads off a figure, how it travels
inside the PNG, and what the page extractor writes out.

A digest that silently dropped a series, mislabelled a bar, or lost its
figure's caption would still leave a well-formed markdown file, so each
piece is checked against a figure whose content is known.
"""

import datetime as dt
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from IPython.core.pylabtools import print_figure

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


def test_digest_travels_in_the_png_and_comes_back_intact():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9], label='sq')
    ax.set_title('t')
    d = digest.describe(fig)
    png = print_figure(fig, fmt='png', base64=False, metadata={digest.KEY: digest.encode(d)})
    assert digest.from_png(png) == json.loads(json.dumps(d))
    assert digest.from_png(print_figure(fig, fmt='png', base64=False)) is None


def test_digest_travels_in_a_pdf_and_comes_back_intact():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9], label='sq')
    d = digest.describe(fig)
    with digest._quiet_pdf_metadata():
        pdf = print_figure(fig, fmt='pdf', base64=False, metadata=digest.pdf_metadata(d))
    assert digest.from_pdf(pdf) == json.loads(json.dumps(d))
    assert digest.from_image(pdf) == digest.from_pdf(pdf)
    assert digest.from_pdf(print_figure(fig, fmt='pdf', base64=False)) is None
    with pytest.raises(ValueError):
        digest.from_image(b'GIF89a')


def test_a_long_digest_is_split_across_pdf_entries(monkeypatch):
    monkeypatch.setattr(digest, 'PDF_PIECE', 40)
    fig, ax = plt.subplots()
    ax.plot(range(50), range(50), label='s')
    d = digest.describe(fig)
    meta = digest.pdf_metadata(d)
    assert len(meta) > 2 and digest.KEY in meta and f'{digest.KEY}-2' in meta
    with digest._quiet_pdf_metadata():
        pdf = print_figure(fig, fmt='pdf', base64=False, metadata=meta)
    assert digest.from_pdf(pdf) == json.loads(json.dumps(d))


def test_save_writes_pdfs_that_carry_the_digest(tmp_path):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2], label='s')
    ft.label_lines(ax)
    written = ft.save(fig, tmp_path / 'fig')
    assert len(written) == 2
    for path in written:
        assert digest.from_pdf(path.read_bytes())['panels'][0]['series'][0]['label'] == 's'


def test_figure_command_prints_what_an_image_carries(tmp_path, capsys):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    ft.save(fig, tmp_path / 'fig', targets=('paper',))
    assert digest.main(['figure', str(tmp_path / 'fig.pdf')]) == 0
    out = capsys.readouterr().out
    assert out.startswith('### fig\n') and '| x | s |' in out
    (tmp_path / 'blank.pdf').write_bytes(print_figure(fig, fmt='pdf', base64=False))
    assert digest.main(['figure', str(tmp_path / 'blank.pdf')]) == 1


def two_figure_pdf(tmp_path):
    """A stand-in for a rendered document: two figure PDFs' bytes in one file."""
    fig1, ax1 = plt.subplots()
    ax1.plot([1, 2], [1, 2], label='first')
    fig2, ax2 = plt.subplots()
    ax2.bar(['a', 'b'], [1, 2], label='second')
    with digest._quiet_pdf_metadata():
        parts = [print_figure(f, fmt='pdf', base64=False,
                              metadata=digest.pdf_metadata(digest.describe(f)))
                 for f in (fig1, fig2)]
    doc = tmp_path / 'doc.pdf'
    doc.write_bytes(b''.join(parts))
    return doc


def test_a_document_pdf_yields_every_figure_in_order(tmp_path, capsys):
    doc = two_figure_pdf(tmp_path)
    found = digest.pdf_digests(doc.read_bytes())
    assert [d['panels'][0]['series'][0]['label'] for d in found] == ['first', 'second']
    assert digest.main(['figure', str(doc)]) == 0
    out = capsys.readouterr().out
    assert '### doc, figure 1' in out and '### doc, figure 2' in out


def test_extract_pdf_writes_the_figures_beside_the_page_digest(tmp_path):
    (tmp_path / '_quarto.yml').write_text('project: {}\n', encoding='utf-8')
    src = tmp_path / 'doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    doc = two_figure_pdf(tmp_path)
    cache = tmp_path / 'cache'
    out = digest.digest_dir(tmp_path, src, cache)
    summary = digest.extract_pdf(doc, out / 'pdf', source=src, root=tmp_path)
    assert summary == '2 figures from the PDF'
    text = (out / 'pdf' / 'outputs.md').read_text(encoding='utf-8')
    assert text.startswith('# Figures of doc.qmd, from its PDF render')
    assert '### figure-1' in text and '### figure-2' in text and '| category | second |' in text
    assert (out / 'pdf' / 'figure-2.csv').exists()
    assert digest.check(src, cache=cache) == 0   # the PDF render's manifest stands in
    html = tmp_path / 'doc.html'
    html.write_text('<main><h2>S</h2></main>', encoding='utf-8')
    digest.extract(html, out, source=src, root=tmp_path)
    assert (out / 'outputs.md').exists() and (out / 'pdf' / 'outputs.md').exists()


def test_setup_arms_the_notebook_formatters_once():
    # In its own interpreter: enabling the inline backend is process-wide.
    # Formats are selected before setup, as a Quarto render does, and the
    # shell stands in for a kernel's, which carries the kernel it serves.
    code = textwrap.dedent('''
        import base64, json
        import matplotlib
        matplotlib.use('module://matplotlib_inline.backend_inline')
        from IPython.core.interactiveshell import InteractiveShell
        ip = InteractiveShell.instance()
        ip.kernel = object()
        from matplotlib_inline.backend_inline import set_matplotlib_formats
        set_matplotlib_formats('png', 'pdf')
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        import figtools as ft
        from figtools import digest
        fmts = ip.display_formatter.formatters
        plain = [fmts[m].lookup_by_type(Figure) for m in ('image/png', 'application/pdf')]
        ft.setup()
        first = fmts['image/png'].lookup_by_type(Figure)
        assert getattr(first, '_figtools_digest', False)
        assert getattr(fmts['application/pdf'].lookup_by_type(Figure), '_figtools_digest', False)
        assert digest.installed()
        ft.setup()
        assert fmts['image/png'].lookup_by_type(Figure) is first
        fig, ax = plt.subplots()
        ax.plot([1, 2], [3, 4], label='s')
        d = digest.from_png(base64.b64decode(first(fig)))
        pdf = fmts['application/pdf'].lookup_by_type(Figure)(fig)
        assert digest.from_pdf(base64.b64decode(pdf)) == d
        assert digest.uninstall() and not digest.installed()
        assert [fmts[m].lookup_by_type(Figure) for m in ('image/png', 'application/pdf')] == plain
        assert digest.from_png(base64.b64decode(fmts['image/png'].lookup_by_type(Figure)(fig))) is None
        print(json.dumps(d['panels'][0]['series'][0]['label']))
        ''')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == '"s"'


def fake_shell(kernel=True):
    """An IPython shell with real PNG and PDF formatter registries, a kernel
    or none, and an `enable_matplotlib` that records its calls."""
    from functools import partial
    from types import SimpleNamespace

    from IPython.core.formatters import PDFFormatter, PNGFormatter
    from matplotlib.figure import Figure

    # The shape the inline backend registers: print_figure with keywords.
    png = partial(print_figure, fmt='png', base64=False)
    pdf = partial(print_figure, fmt='pdf', base64=False)
    fmts = {'image/png': PNGFormatter(), 'application/pdf': PDFFormatter()}
    fmts['image/png'].for_type(Figure, png)
    fmts['application/pdf'].for_type(Figure, pdf)
    calls = []
    ip = SimpleNamespace(display_formatter=SimpleNamespace(formatters=fmts),
                         enable_matplotlib=calls.append)
    if kernel:
        ip.kernel = object()
    return ip, calls, (png, pdf)


def pending_backend(monkeypatch, ip):
    """The state of a fresh shell: no backend chosen yet, and `ip` running."""
    import IPython.core.getipython
    monkeypatch.setattr(IPython.core.getipython, 'get_ipython', lambda: ip)
    monkeypatch.setattr(matplotlib, 'get_backend', lambda *a, **k: None)


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
    assert [fmts[m].lookup_by_type(Figure) for m in ('image/png', 'application/pdf')] == list(plain)


def test_uninstall_puts_back_what_install_wrapped(monkeypatch):
    import matplotlib_inline.backend_inline as inline
    from matplotlib.figure import Figure
    ip, calls, (png, pdf) = fake_shell()
    pending_backend(monkeypatch, ip)
    # The inline backend's configuration has run, as it has in a notebook.
    monkeypatch.setattr(inline.configure_inline_support, 'current_backend', 'inline', raising=False)
    fmts = ip.display_formatter.formatters
    assert not digest.installed() and digest.uninstall() is False
    assert digest.install() is True and calls == []
    assert digest.installed()
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    assert digest.from_png(fmts['image/png'].lookup_by_type(Figure)(fig)) is not None
    assert digest.uninstall() is True
    assert fmts['image/png'].lookup_by_type(Figure) is png
    assert fmts['application/pdf'].lookup_by_type(Figure) is pdf
    assert not digest.installed() and digest.uninstall() is False
    # with only one of the two wrapped, the digest is still armed
    digest._wrap_formatter(fmts['application/pdf'], 'pdf')
    assert digest.installed()


def test_installed_and_uninstall_are_safe_outside_ipython(monkeypatch):
    import IPython.core.getipython
    monkeypatch.setattr(IPython.core.getipython, 'get_ipython', lambda: None)
    assert digest.installed() is False and digest.uninstall() is False and digest.install() is False


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
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [2, 4, 6], label='double')
    png = print_figure(fig, fmt='png', base64=True,
                       metadata={digest.KEY: digest.encode(digest.describe(fig))})
    plain = print_figure(fig, fmt='png', base64=True)
    src = tmp_path / 'doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = tmp_path / 'doc.html'
    html.write_text(PAGE.format(png=png, plain=plain), encoding='utf-8')
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
    assert '### fig-2\n\nNo digest in this image' in text
    assert (out / 'fig-double.png').exists() and (out / 'fig-double.csv').exists()
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
</main></body></html>
'''


def test_extract_reads_images_linked_beside_the_page(tmp_path):
    """Unless a page embeds its resources, Quarto links each figure's file,
    written to `<document>_files/figure-html/` beside the page, inside the
    output folder when there is one."""
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [2, 4, 6], label='double')
    png = print_figure(fig, fmt='png', base64=False,
                       metadata={digest.KEY: digest.encode(digest.describe(fig))})
    site = tmp_path / '_site'
    figures = site / 'my doc_files' / 'figure-html'
    figures.mkdir(parents=True)
    (figures / 'fig-linked-output-1.png').write_bytes(png)
    src = tmp_path / 'my doc.qmd'
    src.write_text('---\ntitle: T\n---\n', encoding='utf-8')
    html = site / 'my doc.html'
    html.write_text(LINKED, encoding='utf-8')
    out = tmp_path / 'cache' / 'proj' / 'my doc'
    summary, _ = digest.extract(html, out, source=src, root=tmp_path)
    assert summary == '3 figures, 0 tables, 0 text blocks'
    text = (out / 'outputs.md').read_text(encoding='utf-8')
    assert '### fig-linked. Figure 1: Linked.' in text and '| x | double |' in text
    assert (out / 'fig-linked.png').read_bytes() == png and (out / 'fig-linked.csv').exists()
    gone = figures.resolve() / 'gone.png'
    assert f'### fig-2\n\nImage file {gone} not found; nothing digested.' in text
    assert ('### fig-3\n\nImage linked by the absolute address https://example.org/fig.png, '
            'which is not fetched; nothing digested.') in text
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['figures'] == [{'label': 'fig-linked', 'digest': True,
                                    'files': ['fig-linked.png', 'fig-linked.csv']}]


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
    two_figure_pdf(site)   # doc.pdf, the same document rendered to PDF
    (site / 'search.json').write_text('{}', encoding='utf-8')
    quarto_env(monkeypatch, tmp_path, output_dir,
               ['doc.html', 'doc.pdf', str(Path('sub') / 'other.html'), 'search.json'])
    cache = tmp_path / 'cache'
    monkeypatch.setenv('FIGTOOLS_DIGEST_DIR', str(cache))   # read when post_render runs
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
    two_figure_pdf(site).rename(site / 'My-Book.pdf')
    two_figure_pdf(site).rename(site / 'about.pdf')
    quarto_env(monkeypatch, tmp_path, '_site', ['doc.html', 'about.html', 'My-Book.pdf',
                                                'about.pdf'])
    cache = tmp_path / 'cache'
    digest.post_render(cache=cache)
    out = capsys.readouterr().out.splitlines()
    page, book, pdf = (Path('_site', f) for f in ('about.html', 'My-Book.pdf', 'about.pdf'))
    assert f'digest: skipped {page}: its source about.md runs no figtools code' in out
    assert (f"digest: skipped {book}: no single document made it, as with a book's PDF; "
            f'render-digest figure {book} prints the digests it carries') in out
    assert (f'digest: skipped {pdf}: its source about.md runs no figtools code; '
            f'render-digest figure {pdf} prints the digests it carries') in out
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
        for argv in (['check', str(src)], ['where', str(src)]):
            assert digest.main(argv) == 2, (value, argv)
            err = capsys.readouterr().err
            assert err.startswith(f'render-digest {argv[0]}: ')
            assert 'FIGTOOLS_DIGEST_DIR' in err and err.count('\n') == 1
