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

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from IPython.core.pylabtools import print_figure

import plotsmith as ps
from plotsmith import digest


@pytest.fixture(autouse=True)
def style():
    ps.setup()
    yield
    plt.close('all')


def panel(fig):
    return digest.describe(fig)['panels'][0]


def test_lines_rules_and_shades_are_read_with_their_labels():
    fig, ax = plt.subplots()
    years = list(range(1990, 1995))
    ax.plot(years, [1.0, 1.1, 1.2, 1.15, 1.3], label='Portugal')
    ax.plot(years, [1.0, 0.9, 0.95, 1.0, 1.05], label='North')
    ps.refline(ax, 1, 'parity')
    ps.shade(ax, 1991, 1992, 'fragile')
    ps.label_lines(ax)
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


def test_stacked_bars_carry_their_base():
    fig, ax = plt.subplots()
    ax.bar([1, 2], [1, 2])
    ax.bar([1, 2], [3, 4], bottom=[1, 2])
    s = panel(fig)['series']
    assert 'base' not in s[0] and s[1]['base'] == [1, 2] and s[1]['value'] == [3, 4]


def test_dotplot_reads_positions_intervals_and_the_category_labels():
    fig, ax = plt.subplots()
    ps.dotplot(ax, ['a', 'b', 'c'], [0.2, -0.1, 0.5], ci=([0.1, -0.3, 0.3], [0.3, 0.1, 0.7]))
    p = panel(fig)
    pts = next(s for s in p['series'] if s['kind'] == 'points')
    assert pts['y'] == ['b', 'a', 'c'] and pts['x'] == [-0.1, 0.2, 0.5]   # rows sorted by value
    iv = next(s for s in p['series'] if s['kind'] == 'intervals')
    assert iv['orientation'] == 'horizontal'
    assert iv['at'] == ['b', 'a', 'c'] and iv['lo'] == [-0.3, 0.1, 0.3] and iv['hi'] == [0.1, 0.3, 0.7]


def test_dotplot_stems_are_furniture_and_its_table_names_rows_by_category():
    fig, ax = plt.subplots()
    ps.dotplot(ax, ['a', 'b'], [0.2, -0.1])
    d = digest.describe(fig)
    assert [s['kind'] for s in d['panels'][0]['series']] == ['points']
    text = '\n'.join(digest.markdown(d, 'fig-d'))
    assert '| y | unlabelled points 1 |' in text and '| b | -0.1 |' in text
    assert '**Panel 1**.' in text   # bare axes are not described


def test_pointrange_is_a_marker_series_with_vertical_intervals():
    fig, ax = plt.subplots()
    ps.pointrange(ax, [1, 2, 3], [0.1, 0.2, 0.3], [0, 0.1, 0.2], [0.2, 0.3, 0.4], label='beta')
    p = panel(fig)
    assert [s['kind'] for s in p['series']] == ['points', 'intervals']
    assert p['series'][0]['label'] == 'beta'
    assert p['series'][1]['orientation'] == 'vertical' and p['series'][1]['hi'] == [0.2, 0.3, 0.4]


def test_band_takes_the_name_of_its_line_and_errorbars_keep_their_bounds():
    fig, ax = plt.subplots()
    x = [1, 2, 3]
    ax.plot(x, [1, 2, 3], label='fit')
    ps.band(ax, x, [0.5, 1.5, 2.5], [1.5, 2.5, 3.5])
    ax.errorbar([1, 2], [5, 6], yerr=[0.5, 1], label='obs', fmt='o')
    p = panel(fig)
    band = next(s for s in p['series'] if s['kind'] == 'band')
    assert band['label'] == 'band: fit'
    assert band['x'] == x and band['lo'] == [0.5, 1.5, 2.5] and band['hi'] == [1.5, 2.5, 3.5]
    eb = next(s for s in p['series'] if s['kind'] == 'errorbar')
    assert eb['label'] == 'obs' and eb['lo'] == [4.5, 5] and eb['hi'] == [5.5, 7]
    assert not any(s['kind'] == 'points' for s in p['series'])   # the errorbar's marker line only once


def test_scatter_labelled_points_and_notes():
    fig, ax = plt.subplots()
    ax.scatter([1, 2, 3], [3, 1, 2], label='obs')
    ps.label_points(ax, [1, 2], [3, 1], ['A', 'B'])
    ps.label_scatter(ax, [4, 5], [4, 5], ['C', 'D'], note=True)
    p = panel(fig)
    assert [s['kind'] for s in p['series']] == ['points'] and p['series'][0]['x'] == [1, 2, 3]
    assert [(q['text'], q['x'], q['y']) for q in p['points']] == [
        ('A', 1, 3), ('B', 2, 1), ('C', 4, 4), ('D', 5, 5)]
    assert p['notes'][0]['text'].startswith('N = 2')


def test_key_entries_name_unlabelled_series_by_colour():
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2], color=ps.PALETTE[0])
    ax.plot([1, 2], [2, 1], color=ps.PALETTE[1])
    ps.key(ax, [('first', ps.PALETTE[0]), ('second', ps.PALETTE[1])])
    p = panel(fig)
    assert [s['label'] for s in p['series']] == ['first', 'second']
    assert all(s['named_by'] == 'key' for s in p['series'])


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


def test_log_scales_and_guides_are_marked():
    fig, ax = plt.subplots()
    ax.plot([1, 10, 100], [1, 2, 3], label='s')
    ax.set_xscale('log')
    ps.guide(ax, [1, 100], [1, 3])
    p = panel(fig)
    assert p['xscale'] == 'log'
    assert [s.get('role') for s in p['series']] == [None, 'guide']


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
    ps.label_lines(ax)
    written = ps.save(fig, tmp_path / 'fig')
    assert len(written) == 2
    for path in written:
        assert digest.from_pdf(path.read_bytes())['panels'][0]['series'][0]['label'] == 's'


def test_figure_command_prints_what_an_image_carries(tmp_path, capsys):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4], label='s')
    ps.save(fig, tmp_path / 'fig', targets=('paper',))
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
    # Formats are selected before setup, as a Quarto render does.
    code = textwrap.dedent('''
        import base64, json
        import matplotlib
        matplotlib.use('module://matplotlib_inline.backend_inline')
        from IPython.core.interactiveshell import InteractiveShell
        ip = InteractiveShell.instance()
        from matplotlib_inline.backend_inline import set_matplotlib_formats
        set_matplotlib_formats('png', 'pdf')
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        import plotsmith as ps
        from plotsmith import digest
        ps.setup()
        fmts = ip.display_formatter.formatters
        first = fmts['image/png'].lookup_by_type(Figure)
        assert getattr(first, '_plotsmith_digest', False)
        assert getattr(fmts['application/pdf'].lookup_by_type(Figure), '_plotsmith_digest', False)
        ps.setup()
        assert fmts['image/png'].lookup_by_type(Figure) is first
        fig, ax = plt.subplots()
        ax.plot([1, 2], [3, 4], label='s')
        d = digest.from_png(base64.b64decode(first(fig)))
        pdf = fmts['application/pdf'].lookup_by_type(Figure)(fig)
        assert digest.from_pdf(base64.b64decode(pdf)) == d
        print(json.dumps(d['panels'][0]['series'][0]['label']))
        ''')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == '"s"'


def test_markdown_merges_series_on_a_shared_x_and_thins_large_tables():
    fig, ax = plt.subplots()
    x = list(range(300))
    ax.plot(x, x, label='a')
    ax.plot(x, [2 * v for v in x], label='b')
    ax.plot([0, 1], [5, 6], label='c')
    ps.refline(ax, 1, 'one')
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
    ps.refline(ax, 3.5, 'mid')
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
    summary = digest.extract(html, out, source=src, root=tmp_path)
    assert summary == '2 figures, 1 table, 1 text block'
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
