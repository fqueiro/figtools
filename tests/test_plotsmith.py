"""Tests for the plotsmith package.

Every failure this guards against is silent. A label nudged apart from its
neighbours can be pushed outside the panel and simply not appear; a label can
come to rest on top of a line, or on another label, and still render. Nothing
raises and the figure still looks finished, so only measuring it catches any
of this.

`ps.collisions` is the measurement, and it is used here both as the thing
under test and as the check on the placement: the two are tested against each
other, since a detector that never fires would pass a chart full of overlaps.
"""

from itertools import pairwise

import matplotlib
import numpy as np
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt

import plotsmith as ps
from plotsmith import _spread

LO, HI = 0.02, 0.98


@pytest.mark.parametrize('ys', [
    [0.065, 0.078, 0.060, 0.063],   # four series finishing almost together
    [0.1, 0.4, 0.7],                # already far enough apart
    [0.5, 0.5, 0.5],                # exactly coincident
    [0.5] * 20,                     # more labels than can fit at full gap
    [0.98, 0.99, 0.97],             # pressed against the top
    [0.01, 0.0, 0.02],              # pressed against the bottom
    [0.5],                          # single series
])
def test_spread_keeps_labels_inside_the_panel(ys):
    out = _spread(ys, 0.075, lo=LO, hi=HI)
    assert len(out) == len(ys)
    assert min(out) >= LO - 1e-9
    assert max(out) <= HI + 1e-9


@pytest.mark.parametrize('ys', [
    [0.065, 0.078, 0.060, 0.063],
    [0.1, 0.4, 0.7],
    [0.9, 0.1, 0.5, 0.3],
])
def test_spread_preserves_the_vertical_order_of_series(ys):
    """A label must never cross the series above or below it."""
    out = _spread(ys, 0.075, lo=LO, hi=HI)
    assert (np.argsort(np.argsort(ys)) == np.argsort(np.argsort(out))).all()


def test_spread_separates_coincident_labels():
    out = sorted(_spread([0.5, 0.5, 0.5], 0.075, lo=LO, hi=HI))
    assert all(b - a >= 0.075 - 1e-9 for a, b in pairwise(out))


def _labels(ax):
    return {t.get_text() for t in ax.texts}


def test_label_lines_labels_every_series():
    """The doc-01 aggregation figure: four series ending within a hair."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(1954, 2024)
    for name, level in [('sum of inputs', 0.744), ('equal', 0.757),
                        ('gdp', 0.739), ('pop', 0.742)]:
        ax.plot(x, np.linspace(1.5, level, len(x)), label=name)
    ax.set_xlim(1954, 2024)
    ps.label_lines(ax)
    assert _labels(ax) == {'sum of inputs', 'equal', 'gdp', 'pop'}
    plt.close(fig)


def test_label_lines_puts_an_early_ending_series_at_its_own_end():
    """A series stopping mid-panel is labelled there, not in the margin."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(1985, 2025)
    ax.plot(x, np.full(x.shape, 5.0), label='full span')
    early = np.arange(1985, 1996)
    ax.plot(early, np.full(early.shape, 3.9), label='ends early')
    ax.set_xlim(1985, 2025)
    ps.label_lines(ax)
    pos = {t.get_text(): t.get_position()[0] for t in ax.texts}
    assert pos['ends early'] < 1996 + 2
    assert pos['full span'] > 2020
    plt.close(fig)


def test_label_lines_keeps_ticks_off_the_label_margin():
    """The space added for labels is not data space, so it carries no ticks."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(1954, 2024)
    ax.plot(x, np.linspace(0, 1, len(x)), label='a')
    ax.set_xlim(1954, 2024)
    ps.label_lines(ax, room=0.3)
    assert max(ax.get_xticks()) <= 2024
    assert ax.get_xlim()[1] > 2024
    plt.close(fig)


def test_label_lines_rejects_an_axes_with_nothing_labelled():
    ps.setup()
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2])          # no label: not a data series
    with pytest.raises(ValueError):
        ps.label_lines(ax)
    plt.close(fig)


def _short_and_long(ax):
    """A long series plus one stopping early, whose ends sit close together.

    The doc-10 shape: the short series' label is placed mid-panel, where the
    long series' path runs straight through it.
    """
    yr = np.arange(1985, 2024)
    ax.plot(yr, 1.0 + 0.010 * (yr - 1985), label='Portugal')
    early = np.arange(1985, 1996)
    ax.plot(early, 1.0 + 0.011 * (early - 1985), label='North\nexcl. DE')
    ax.set_xlim(1985, 2023)


def test_collisions_catches_a_label_sitting_on_a_line():
    """The detector must fire, or every other check here passes vacuously."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    _short_and_long(ax)
    ax.annotate('North excl. DE', xy=(1996, 1.11), xycoords='data',
                fontsize=8.5, va='center')
    hits = ps.collisions(fig)
    assert any(kind == 'label/line' for _, kind, _ in hits)
    plt.close(fig)


def test_label_lines_moves_a_label_off_another_series_line():
    ps.setup()
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    _short_and_long(ax)
    ps.label_lines(ax)
    plt.tight_layout()
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_band_and_reference_labels_end_up_clear_of_the_data():
    """A band label has to clear a series that fills the band beneath it."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    yr = np.arange(1954, 2024)
    ax.plot(yr, 1.45 - 0.008 * (yr - 1954)
            + 0.20 * np.exp(-((yr - 1969) / 4.0) ** 2), label='Northwest')
    ax.set_xlim(1954, 2024)
    ps.refline(ax, 1, 'parity')
    ps.shade(ax, 1950, 1985, 'fragile data')
    ps.label_lines(ax)
    plt.tight_layout()
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_a_label_that_cannot_move_is_rejected_rather_than_left_in_place():
    """Sliding a label moves its anchor, which only carries the text along
    when the text is positioned as an offset from it. Anchoring it any other
    way used to leave the placement quietly optimising a box that never
    moved."""
    ps.setup()
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='a')
    ann = ax.annotate('pinned', xy=(0.5, 0.9), xycoords='axes fraction')
    with pytest.raises(ValueError, match='offset'):
        ps._slide(fig.canvas.get_renderer(), ax=ax, ann=ann, axis='x',
                  order=np.arange(0.9, 0.1, -0.1), fixed=[])
    plt.close(fig)


def test_label_points_separates_a_cluster():
    ps.setup()
    fig, ax = plt.subplots(figsize=(5, 4))
    x = [3.0, 3.4, 4.1, 2.8, 9.0, -12.0]
    y = [17.0, 17.6, 18.0, 13.5, 18.0, 0.5]
    names = ['Netherlands', 'United Kingdom', 'Finland', 'France',
             'Germany', 'Italy']
    ax.scatter(x, y, s=18)
    ps.label_points(ax, x, y, names)
    plt.tight_layout()
    assert ps.collisions(fig) == []
    assert {t.get_text() for t in ax.texts} == set(names)
    plt.close(fig)


def test_a_marker_only_series_occupies_its_markers_not_the_gaps():
    """Joining up scattered markers would invent segments never drawn, and
    push labels out of space that is in fact empty."""
    ps.setup()
    fig, ax = plt.subplots()
    dots, = ax.plot([0, 10], [0, 10], lw=0, marker='o', label='dots')
    line, = ax.plot([0, 10], [0, 10], label='line')
    assert len(ps._curve(ax, dots)) == 2
    assert len(ps._curve(ax, line)) > 50
    plt.close(fig)


def test_data_lines_excludes_reference_rules_and_guides():
    ps.setup()
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='series')
    ax.axhline(0.5)
    ax.axvline(0.5)
    ps.guide(ax, [0, 1], [1, 0])
    assert [ln.get_label() for ln in ps._data_lines(ax)] == ['series']
    plt.close(fig)


def test_key_moves_to_a_corner_the_data_leaves_free():
    ps.setup()
    fig, ax = plt.subplots(figsize=(5, 3))
    x = np.linspace(0, 1, 50)
    # Fills the lower left, leaving the upper left free.
    ax.plot(x, 1 - x, color=ps.PALETTE[0], label='a')
    ax.plot(x, 0.9 - x, color=ps.PALETTE[1], label='b')
    ps.key(ax, [('a', ps.PALETTE[0]), ('b', ps.PALETTE[1])])
    plt.tight_layout()
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_key_entries_clear_each_other_in_a_small_panel():
    """The grid-of-small-multiples case: the step must come from measured
    text, or big type in a little panel overprints the stack."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(2.0, 1.6))
    x = np.linspace(0, 1, 30)
    ax.plot(x, 0.05 + 0.02 * x, color=ps.PALETTE[0], label='a')
    ax.set_ylim(0, 1)   # keep the line at the bottom, corners free
    ps.key(ax, [('leaders', ps.PALETTE[0]), ('envelope', ps.PALETTE[0]),
                ('EU', ps.GREY['rule']), ('Portugal', ps.PALETTE[1])])
    plt.tight_layout()
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_dotplot_sorts_values_and_carries_labels_and_colours_along():
    ps.setup()
    fig, ax = plt.subplots()
    ps.dotplot(ax, ['a', 'b', 'c'], [0.3, -0.1, 0.2],
               color=[ps.PALETTE[0], ps.PALETTE[1], ps.PALETTE[2]])
    assert [t.get_text() for t in ax.get_yticklabels()] == ['b', 'c', 'a']
    xs = ax.collections[0].get_offsets()[:, 0]
    assert list(xs) == sorted(xs)
    plt.close(fig)


def test_dotplot_rejects_a_mismatched_colour_list():
    ps.setup()
    fig, ax = plt.subplots()
    with pytest.raises(ValueError):
        ps.dotplot(ax, ['a', 'b'], [1.0, 2.0], color=[ps.PALETTE[0]])
    plt.close(fig)


def test_setup_derives_every_text_size_from_the_base():
    """One knob: annotations, key entries and ticks all move with `base`."""
    ps.setup(base=12.0)
    assert plt.rcParams['axes.labelsize'] == 12.0
    assert plt.rcParams['xtick.labelsize'] == 11.5
    assert plt.rcParams['legend.fontsize'] == 11.5
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], color=ps.PALETTE[0], label='a')
    ps.key(ax, [('a', ps.PALETTE[0])])
    ps.refline(ax, 0.5, 'half')
    assert {t.get_fontsize() for t in ax.texts} == {11.5}
    plt.close(fig)
    ps.setup()   # restore the default for the tests that follow


def test_band_inherits_the_colour_of_the_series_it_wraps():
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(10.0)
    ax.plot(x, x, color=ps.PALETTE[1], label='s')
    poly = ps.band(ax, x, x - 1, x + 1)
    got = matplotlib.colors.to_hex(poly.get_facecolor()[0])
    assert got == ps.PALETTE[1].lower()
    plt.close(fig)


def test_band_without_a_line_requires_an_explicit_colour():
    ps.setup()
    fig, ax = plt.subplots()
    with pytest.raises(ValueError, match='colour'):
        ps.band(ax, [0, 1], [0, 0], [1, 1])
    plt.close(fig)


def test_band_rejects_inverted_bounds():
    ps.setup()
    fig, ax = plt.subplots()
    with pytest.raises(ValueError, match='lower bound'):
        ps.band(ax, [0, 1], [1, 1], [0, 0])
    plt.close(fig)


def test_pointrange_is_labelled_like_any_series_and_checks_clean():
    """The event-study recipe: pointrange + zero line + treatment marker."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    t = np.arange(-4, 5)
    b = 0.05 * t
    ln = ps.pointrange(ax, t, b, b - 0.1, b + 0.1, label='effect')
    ps.refline(ax, 0)
    ps.refline(ax, -0.5, axis='x')
    ps.label_lines(ax)
    plt.tight_layout()
    assert ps.collisions(fig) == []
    assert ln in ps._plotted_lines(ax)
    plt.close(fig)


def test_pointrange_rejects_inverted_bounds():
    ps.setup()
    fig, ax = plt.subplots()
    with pytest.raises(ValueError, match='lower bound'):
        ps.pointrange(ax, [0], [1.0], [2.0], [0.5])
    plt.close(fig)


def test_dotplot_with_intervals_reorders_them_with_the_values():
    ps.setup()
    fig, ax = plt.subplots()
    ps.dotplot(ax, ['a', 'b'], [2.0, 1.0], ci=([1.5, 0.4], [2.5, 1.6]))
    segs = ax.collections[0].get_segments()
    # Sorted ascending, so row 0 is 'b' and carries b's interval.
    assert segs[0][0][0] == pytest.approx(0.4)
    assert segs[1][0][0] == pytest.approx(1.5)
    plt.close(fig)


def test_label_scatter_marks_are_data_not_labels():
    """Coincident names are overlapping observations, not a placement bug."""
    ps.setup()
    fig, ax = plt.subplots()
    ps.label_scatter(ax, [1.0, 1.001, 3.0], [1.0, 1.001, 3.0],
                     ['a', 'b', 'c'], fit=True, note=True)
    plt.tight_layout()
    assert ps.collisions(fig) == []
    assert ax.get_xlim()[1] >= 3.0   # text alone does not autoscale
    plt.close(fig)


def test_binscatter_bins_and_recovers_the_slope():
    ps.setup()
    fig, ax = plt.subplots()
    rng = np.random.default_rng(0)
    x = rng.normal(size=400)
    y = 2.0 * x + rng.normal(scale=0.1, size=400)
    xm, ym, slope = ps.binscatter(ax, x, y, bins=20)
    assert len(xm) == 20
    assert slope == pytest.approx(2.0, abs=0.05)
    plt.close(fig)


def test_binscatter_controls_remove_a_confounder():
    ps.setup()
    fig, ax = plt.subplots()
    rng = np.random.default_rng(1)
    c = rng.normal(size=500)
    x = c + rng.normal(scale=0.5, size=500)
    y = 3.0 * c + rng.normal(scale=0.1, size=500)
    _, _, raw = ps.binscatter(ax, x, y, bins=10)
    _, _, partial = ps.binscatter(ax, x, y, controls=c, bins=10)
    assert abs(partial) < 0.1 < abs(raw)
    plt.close(fig)


def test_binscatter_refuses_missings():
    ps.setup()
    fig, ax = plt.subplots()
    with pytest.raises(ValueError, match='missings'):
        ps.binscatter(ax, [1.0, np.nan], [1.0, 2.0])
    plt.close(fig)


def test_panels_keep_the_document_width():
    ps.setup()
    fig, axes = ps.panels(2)
    assert fig.get_size_inches()[0] == pytest.approx(
        plt.rcParams['figure.figsize'][0])
    assert len(axes) == 2
    plt.close(fig)


def test_save_writes_each_target_and_restores_the_figure(tmp_path):
    ps.setup()
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    yr = np.arange(2000, 2020)
    ax.plot(yr, np.linspace(1, 2, 20), label='a')
    ax.plot(yr, np.linspace(2, 1, 20), label='b')
    ps.label_lines(ax)
    ps.save(fig, tmp_path / 'demo', retouch=True)
    assert (tmp_path / 'demo.pdf').exists()
    assert (tmp_path / 'demo_slides.pdf').exists()
    assert (tmp_path / 'demo.pkl').exists()
    assert tuple(fig.get_size_inches()) == pytest.approx((6.4, 4.8))
    back = ps.load(tmp_path / 'demo.pkl')
    assert ps.collisions(back) == []   # came back live and clean
    plt.close(fig)


def test_save_rejects_an_unknown_target(tmp_path):
    ps.setup()
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='a')
    with pytest.raises(ValueError, match='unknown targets'):
        ps.save(fig, tmp_path / 'x', targets=('poster',))
    plt.close(fig)


def test_save_refuses_a_missing_directory(tmp_path):
    ps.setup()
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='a')
    with pytest.raises(FileNotFoundError):
        ps.save(fig, tmp_path / 'nope' / 'x')
    plt.close(fig)
