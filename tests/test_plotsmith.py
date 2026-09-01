"""Tests for the plotsmith package.

Every failure this guards against is silent. A label nudged apart from its
neighbours can be pushed off the figure and simply not appear; a label can
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


def test_spread_leaves_a_label_with_clear_space_on_its_anchor():
    """Spreading a crowded cluster must not drag distant labels along: the
    old push-then-re-centre shifted the whole block, moving labels whose own
    space was never contested (doc 12: Denmark's label pulled off its line
    because four other series ended in a cluster far below it)."""
    ys = [0.62, 0.47, 0.40, 0.32, 0.31, 0.30]
    out = _spread(ys, 0.052, lo=LO, hi=HI)
    assert out[0] == pytest.approx(0.62)   # isolated at the top: untouched
    assert out[1] == pytest.approx(0.47)   # clear of the cluster: untouched
    assert all(b - a >= 0.052 - 1e-9 for a, b in pairwise(sorted(out)))


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


def test_label_lines_ends_the_panel_at_the_data_and_hangs_labels_beyond_it():
    """The axis used to be stretched to hold the labels, so every gridline
    ran on under them. The panel now ends a hair past the last point, the
    labels sit in the figure margin, and the layout engine keeps them inside
    the figure."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5.9, 3.4))
    x = np.arange(1954, 2024)
    ax.plot(x, np.linspace(0, 1, len(x)), label='a long label at the end')
    ax.plot(x, np.linspace(0, 0.5, len(x)), label='b')
    ps.label_lines(ax)
    assert 2023 < ax.get_xlim()[1] < 2023 + 0.02 * 69
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    panel_right = ax.get_window_extent(r).x1
    for t in ax.texts:
        # The text alone: an annotation's own extent unions in its leader.
        box = matplotlib.text.Text.get_window_extent(t, r)
        assert box.x0 >= panel_right
        assert box.x1 <= fig.get_figwidth() * fig.dpi
    grid_right = max(tk.gridline.get_window_extent(r).x1
                     for tk in ax.yaxis.get_major_ticks())
    assert grid_right <= panel_right + 1
    plt.close(fig)


def test_label_lines_keeps_margin_labels_in_the_order_of_the_line_ends():
    """The ladder is the pairing: the label whose line ends higher must sit
    higher, or the reader pairs every label with the wrong line. This is the
    doc-12 four-panel figure reduced to its geometry (series ending in tight
    clusters, small type, one shared x-axis), which used to come out with
    swapped labels: a label brushing the tail of a neighbouring line was
    kicked into the dodge search, and the search could settle on the far
    side of a neighbour."""
    ps.setup(base=9)
    fig, axes = plt.subplots(2, 2, figsize=(9, 7), sharex=True, sharey=True)
    x = np.arange(1995, 2022)
    t = (x - x[0]) / (x[-1] - x[0])
    panels = [
        {'FI': 131, 'SE': 90, 'DK': 194, 'DE': 86, 'FR': 111, 'PT': 88},
        {'FI': 372, 'SE': 286, 'DK': 500, 'DE': 207, 'FR': 189, 'PT': 86},
        {'FI': 110, 'SE': 214, 'DK': 154, 'DE': 153, 'FR': 120, 'PT': 80},
        {'FI': 183, 'DK': 181, 'DE': 220, 'FR': 189, 'PT': 114},
    ]
    for ax, ends in zip(axes.flat, panels):
        for name, e in ends.items():
            ax.plot(x, 100 * (e / 100) ** t, label=name)
        ax.set_yscale('log')
        ax.set_ylim(40, 520)
        ps.refline(ax, 100, axis='y', ls='-', lw=0.8)
        ps.label_lines(ax)
    fig.canvas.draw()
    for ax, ends in zip(axes.flat, panels):
        anns = [a for a in ax.texts if a.get_text() in ends]
        by_label = [a.get_text() for a in
                    sorted(anns, key=lambda a: -a.get_position()[1])]
        by_end = sorted(ends, key=lambda n: -ends[n])
        assert by_label == by_end
    plt.close(fig)
    ps.setup()


def test_label_lines_puts_a_clear_label_on_its_line_with_no_leader():
    """A series with empty space around its end gets its label right at the
    line, leaderless, even when other series in the panel end in a cluster
    that has to be spread apart."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(2000, 2021)
    t = (x - x[0]) / (x[-1] - x[0])
    for name, e in [('top', 0.90), ('a', 0.50), ('b', 0.48), ('c', 0.46)]:
        ax.plot(x, 0.5 + (e - 0.5) * t, label=name)
    ax.set_xlim(2000, 2020)
    ax.set_ylim(0, 1)
    ps.label_lines(ax)
    fig.canvas.draw()
    top = next(t for t in ax.texts if t.get_text() == 'top')
    assert top.get_position()[1] == pytest.approx(0.90, abs=1e-6)
    assert not top.arrow_patch.get_visible()
    plt.close(fig)


def test_leaders_appear_only_when_a_label_is_clearly_off_its_line():
    """A stub leader on a label still level with its line end is noise (the
    word's colour and the ladder's order carry the pairing); a label pushed
    well off, like the outer labels of a fan over coincident ends, earns
    one. The old fixed 2%-of-panel threshold was well under a label height
    and sprouted stubs all over a spread cluster."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(2000, 2021)
    t = (x - x[0]) / (x[-1] - x[0])
    # A tight cluster: the spread displaces each label by less than its own
    # height, so none of the three needs a leader.
    for name, e in [('a', 0.604), ('b', 0.592), ('c', 0.587)]:
        ax.plot(x, 0.5 + (e - 0.5) * t, label=name)
    # Five series ending together: the outer labels of the fan land well
    # over 1.5 label heights from the shared end and get leaders.
    for name in ['v', 'w', 'x', 'y', 'z']:
        ax.plot(x, np.full(x.shape, 0.15), label=name)
    ax.set_xlim(2000, 2020)
    ax.set_ylim(0, 1)
    ps.label_lines(ax)
    fig.canvas.draw()
    vis = {t.get_text(): t.arrow_patch.get_visible() for t in ax.texts}
    assert not any(vis[k] for k in 'abc')
    assert vis['v'] and vis['z']         # the fan's outer labels
    assert not any(vis[k] for k in 'wxy')
    plt.close(fig)


@pytest.mark.parametrize('first', [0, 1])
def test_label_lines_ends_shared_panels_at_the_latest_line_end(first):
    """Two sharex panels whose series stop in different years, labelled in
    either order: neither call may clip the other panel's lines, so both end
    at the later year, and the shorter series is labelled where it stops."""
    ps.setup()
    fig, axes = plt.subplots(1, 2, sharex=True)
    long, short = np.arange(2000, 2021), np.arange(2000, 2016)
    axes[0].plot(long, np.linspace(1, 2, len(long)), label='a')
    axes[1].plot(short, np.linspace(1, 2, len(short)), label='b')
    for ax in (axes[first], axes[1 - first]):
        ps.label_lines(ax)
    for ax in axes:
        assert 2020 < ax.get_xlim()[1] < 2020 + 0.02 * 20
    pos = {t.get_text(): t.get_position()[0] for t in axes[1].texts}
    assert 2015 < pos['b'] < 2016
    plt.close(fig)


def test_label_lines_rejects_a_series_whose_end_the_limits_clip():
    """A clipped line cannot be labelled at its end: the anchor is off the
    panel, and the figure's limits are what need fixing."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(2000, 2021)
    ax.plot(x, np.linspace(100, 600, len(x)), label='runs off the top')
    ax.plot(x, np.linspace(100, 200, len(x)), label='stays inside')
    ax.set_ylim(40, 520)
    with pytest.raises(ValueError, match='outside the axes limits'):
        ps.label_lines(ax)
    plt.close(fig)


def test_label_lines_keeps_a_limit_set_by_hand():
    """A right limit the figure set itself is its own decision (a horizon
    drawn past the data): it stays, and the label sits at the line end."""
    ps.setup()
    fig, ax = plt.subplots()
    x = np.arange(2000, 2021)
    ax.plot(x, np.linspace(1, 2, len(x)), label='a')
    ax.set_xlim(2000, 2030)
    ps.label_lines(ax)
    assert ax.get_xlim() == (2000, 2030)
    assert 2020 < ax.texts[0].get_position()[0] < 2021
    plt.close(fig)


def test_label_lines_wraps_a_wide_label_per_output_size():
    """A label wider than a fifth of the figure is cut at spaces into the
    fewest balanced lines that fit, and the decision is re-made at every
    draw: a size that has room keeps on one line what a smaller one wraps."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(2023, 2051)
    ax.plot(x, np.linspace(0, 1, len(x)), label='Potencial a longo prazo')
    ax.plot(x, np.linspace(0, 0.5, len(x)), label='Défice')
    ps.label_lines(ax)
    fig.canvas.draw()
    shown = {t._fs_text: t.get_text() for t in ax.texts}
    assert shown['Potencial a longo prazo'] == 'Potencial a longo prazo'
    fig.set_size_inches(4.5, 2.4)
    fig.canvas.draw()
    shown = {t._fs_text: t.get_text() for t in ax.texts}
    assert shown['Potencial a longo prazo'] == 'Potencial a\nlongo prazo'
    assert shown['Défice'] == 'Défice'
    assert ps.collisions(fig) == []
    fig.set_size_inches(10, 4)
    fig.canvas.draw()
    assert {t.get_text() for t in ax.texts} == {'Potencial a longo prazo',
                                                'Défice'}
    plt.close(fig)


def test_label_lines_leaves_a_label_broken_by_hand_alone():
    ps.setup()
    fig, ax = plt.subplots(figsize=(4.1, 2.4))
    x = np.arange(2023, 2051)
    ax.plot(x, np.linspace(0, 1, len(x)), label='per worker, excluding\nGermany')
    ps.label_lines(ax)
    fig.canvas.draw()
    assert ax.texts[0].get_text() == 'per worker, excluding\nGermany'
    plt.close(fig)


def test_label_lines_wrap_none_keeps_a_wide_label_on_one_line():
    ps.setup()
    fig, ax = plt.subplots(figsize=(4.1, 2.4))
    x = np.arange(2023, 2051)
    ax.plot(x, np.linspace(0, 1, len(x)), label='current-PPP expenditure')
    ps.label_lines(ax, wrap=None)
    fig.canvas.draw()
    assert ax.texts[0].get_text() == 'current-PPP expenditure'
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
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_key_avoids_strokes_drawn_as_collections():
    """Dumbbell connectors and dot-plot stems are LineCollections, which the
    corner search used to be blind to: a key could park right on them."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # A data line through the upper left, dumbbell strokes filling the
    # upper right: the first genuinely free corner is the lower right.
    ax.plot([0.0, 0.3], [0.97, 0.85], color=ps.PALETTE[0], label='a')
    ax.hlines([0.85, 0.9, 0.95], 0.6, 1.0, color=ps.GREY['grid'])
    anns = ps.key(ax, [('Portugal', ps.PALETTE[0]),
                       ('North mean', ps.PALETTE[1])])
    fig.canvas.draw()
    assert (anns[0].get_ha(), anns[0].get_va()) == ('right', 'bottom')
    plt.close(fig)


def test_key_avoids_reference_rules():
    """A short direct label may sit across a reference rule; a key block
    parked across one reads as clutter, so the corner search treats rules
    as obstacles."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5, 3))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # Data through the upper left; a vertical rule near the right edge
    # crosses both right-hand corners: only the lower left is clear.
    ax.plot([0.0, 0.3], [0.95, 0.80], color=ps.PALETTE[0], label='a')
    ps.refline(ax, 0.85, axis='x')
    anns = ps.key(ax, [('own deflators', ps.PALETTE[0]),
                       ('harmonized', ps.PALETTE[1])])
    fig.canvas.draw()
    assert (anns[0].get_ha(), anns[0].get_va()) == ('left', 'bottom')
    plt.close(fig)


def test_collisions_sees_collection_strokes():
    """hlines/vlines strokes are data ink: a label sitting on one must be
    reported, or the save-time check waves through dumbbell overlaps."""
    ps.setup()
    fig, ax = plt.subplots()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.hlines([0.5], 0.0, 1.0)
    ax.annotate('on the stem', xy=(0.5, 0.5), xycoords='data',
                ha='center', va='center')
    hits = ps.collisions(fig)
    assert any(kind == 'label/line' for _, kind, _ in hits)
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
    assert ps.collisions(fig) == []
    plt.close(fig)


def test_key_entries_clear_each_other_in_a_tall_panel():
    """The opposite case: in a tall panel the entries are small in axes
    fraction, and a step of 15% of their height leaves less than the
    collision pad between them (the paper's 27-country decomposition, once
    constrained layout let the panel fill the figure)."""
    ps.setup()
    fig, ax = plt.subplots(figsize=(5.9, 6.6))
    x = np.linspace(0, 1, 30)
    ax.plot(x, 0.05 + 0.02 * x, color=ps.PALETTE[0], label='a')
    ax.set_ylim(0, 1)
    ps.key(ax, [('Capital humano', ps.PALETTE[2]),
                ('Capital físico', ps.PALETTE[3]), ('PTF', ps.PALETTE[0])])
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
    assert ps.collisions(fig) == []
    assert ln in ps._plotted_lines(ax)
    # The panel ends past the last estimate by enough that the panel edge
    # does not cut its marker in half.
    r = fig.canvas.get_renderer()
    centre = ax.transData.transform((t[-1], b[-1]))[0]
    radius = ln.get_markersize() / 2 * fig.dpi / 72
    assert centre + radius <= ax.get_window_extent(r).x1
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
