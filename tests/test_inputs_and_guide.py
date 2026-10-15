"""Data frame columns go into the helpers as they are, the README's quick
start runs as written, and the guide ships.

The guide tells a reader to pass Polars or pandas columns without
converting the frame. If a helper came to depend on one library's types or
on a NumPy-only method, that advice would turn into a crash or, worse, a
quietly different figure; drawing each helper both ways and comparing the
digests catches either. The quick start is the first code a stranger runs,
copied from the README, so it is run here as the README prints it. The
guide is the page read before writing code with the package, so it has to
reach an installed copy.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import polars as pl

import figtools as ft
from figtools.digest import describe

RNG = np.random.default_rng(7)
N = 400
FRAME = pl.DataFrame({
    'x': RNG.normal(size=N),
    'c': RNG.normal(size=N),
    'year': np.arange(N) % 20 + 2000,
    'name': [f'n{i}' for i in range(N)],
}).with_columns(
    y=pl.col('x') * 0.5 + pl.col('c') + RNG.normal(size=N),
    lo=pl.col('x') - 0.3,
    hi=pl.col('x') + 0.3,
)


def _draw(columns):
    """One figure using every helper that takes data, fed from `columns`."""
    fig, axes = plt.subplots(1, 4, figsize=(12, 3))
    head = {k: v[:12] for k, v in columns.items()}
    axes[0].plot(head['year'], head['x'], label='series')
    ft.band(axes[0], head['year'], head['lo'], head['hi'])
    ft.pointrange(axes[1], head['year'], head['x'], head['lo'], head['hi'])
    ft.dotplot(axes[2], head['name'], head['x'], ci=(head['lo'], head['hi']))
    slope = ft.binscatter(axes[3], columns['x'], columns['y'],
                          controls=columns['c'])[2]
    return fig, slope


def test_polars_columns_draw_the_same_figure_as_numpy_arrays():
    ft.setup()
    polars_fig, polars_slope = _draw({k: FRAME[k] for k in FRAME.columns})
    numpy_fig, numpy_slope = _draw({k: FRAME[k].to_numpy() for k in FRAME.columns})
    assert polars_slope == numpy_slope
    assert describe(polars_fig) == describe(numpy_fig)
    plt.close('all')


def test_pandas_columns_draw_the_same_figure_as_numpy_arrays():
    # An index that does not start at 0, so a helper that read a Series by
    # label instead of by position would pick the wrong rows or fail.
    frame = pd.DataFrame({k: FRAME[k].to_numpy() for k in FRAME.columns},
                         index=np.arange(N) + 1000)
    ft.setup()
    pandas_fig, pandas_slope = _draw({k: frame[k] for k in frame.columns})
    numpy_fig, numpy_slope = _draw({k: frame[k].to_numpy() for k in frame.columns})
    assert isinstance(frame['x'], pd.Series)
    assert pandas_slope == numpy_slope
    assert describe(pandas_fig) == describe(numpy_fig)
    plt.close('all')


def test_label_scatter_takes_polars_names():
    ft.setup()
    fig, ax = plt.subplots()
    head = FRAME.head(8)
    ft.label_scatter(ax, head['x'], head['y'], head['name'])
    texts = sorted(t.get_text() for t in ax.texts)
    assert texts == sorted(head['name'].to_list())
    plt.close(fig)


def test_a_polars_null_is_a_missing_value():
    y = FRAME['y'].scatter(3, None)
    fig, ax = plt.subplots()
    with pytest.raises(ValueError, match='non-finite'):
        ft.binscatter(ax, FRAME['x'], y)
    plt.close(fig)


def test_group_by_iteration_in_the_guide_keeps_the_sort():
    # The guide's example loops over `df.sort('year').group_by(...,
    # maintain_order=True)`; a line drawn from rows out of order zigzags.
    df = FRAME.select('year', 'x').with_columns(region=pl.col('year') % 3)
    for (_,), d in df.sort('year').group_by('region', maintain_order=True):
        assert d['year'].is_sorted()


README = Path(__file__).resolve().parent.parent / 'README.md'


def test_the_readme_quick_start_runs_as_written(tmp_path):
    # The first python block of the README is the quick start. It runs in a
    # process of its own, since `setup` changes matplotlib for the whole
    # process, from an empty folder, where its `save` writes the two files
    # the README says it writes.
    fence = re.search(r'^```python\n(.*?)^```', README.read_text(encoding='utf-8'),
                      re.MULTILINE | re.DOTALL)
    assert fence, 'README.md has no python code block'
    run = subprocess.run([sys.executable, '-c', fence.group(1)], cwd=tmp_path,
                         env={**os.environ, 'MPLBACKEND': 'Agg'},
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    written = sorted(p.name for p in tmp_path.glob('*.pdf'))
    assert written == ['employment.pdf', 'employment_slides.pdf']


def _module(*args):
    return subprocess.run([sys.executable, '-m', 'figtools', *args],
                          capture_output=True, check=False)


def test_the_module_prints_the_guide_and_the_readme():
    guide = _module()
    assert guide.returncode == 0
    assert guide.stdout.decode('utf-8').startswith('# figtools: how to use it')
    readme = _module('readme')
    assert readme.returncode == 0
    assert readme.stdout.decode('utf-8').lstrip().startswith('# figtools\n')
    assert _module('nonsense').returncode != 0
