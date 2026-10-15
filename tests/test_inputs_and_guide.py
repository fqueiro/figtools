"""Polars columns go into the helpers as they are, and the guide ships.

Projects keep their data in Polars, and the guide tells a reader to pass
columns without converting the frame. If a helper ever came to depend on
pandas or on a NumPy-only method, that advice would turn into a crash or,
worse, a quietly different figure; drawing each helper both ways and
comparing the digests catches either. The guide is the page read before
writing code with the package, so it has to reach an installed copy.
"""

import subprocess
import sys

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt
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


def _module(*args):
    return subprocess.run([sys.executable, '-m', 'figtools', *args],
                          capture_output=True)


def test_the_module_prints_the_guide_and_the_readme():
    guide = _module()
    assert guide.returncode == 0
    assert guide.stdout.decode('utf-8').startswith('# figtools: how to use it')
    readme = _module('readme')
    assert readme.returncode == 0
    assert readme.stdout.decode('utf-8').lstrip().startswith('# figtools\n')
    assert _module('nonsense').returncode != 0
