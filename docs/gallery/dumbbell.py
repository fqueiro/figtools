"""Dumbbell: two measurements of one quantity per row, the baseline filled
and the alternative hollow, from synthetic data.

    uv run python docs/gallery/dumbbell.py

Writes dumbbell.png beside this script and fails if any label overlaps
something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()

    # The gender pay gap by industry, raw and adjusted for education,
    # experience and occupation: two definitions of one quantity, so neither
    # is an interval around the other.
    industries = np.array(['Finance', 'Health', 'Education', 'Retail',
                           'Manufacturing', 'Hospitality', 'Construction',
                           'Public administration'])
    raw = np.array([24.0, 18.0, 9.0, 14.0, 16.0, 7.0, 4.0, 6.0])
    adjusted = np.array([12.0, 10.0, 8.0, 9.0, 11.0, 9.0, 7.0, 6.0])

    # Ranked by the raw gap. dotplot would sort its own rows, but the hollow
    # markers drawn after it have to land on the same rows, so sort here.
    o = np.argsort(raw)
    industries, raw, adjusted = industries[o], raw[o], adjusted[o]
    rows = np.arange(len(raw))

    fig, ax = plt.subplots(figsize=(5.9, 3.4))
    _, dots = ft.dotplot(ax, industries, raw, sort=False,
                         ci=(np.minimum(raw, adjusted),
                             np.maximum(raw, adjusted)))
    # The hollow marks sit over the connector and the filled ones over both,
    # so a row where the two measurements agree shows the baseline's mark.
    # A hollow ring's stroke straddles its outline, so the ring is drawn
    # smaller by its stroke width to come out the size of a filled dot.
    ax.scatter(adjusted, rows, facecolor='white', edgecolor=ft.PALETTE[0],
               linewidths=1.2, s=(5.5 - 1.2) ** 2, zorder=3)
    dots.set_zorder(4)
    # Fill is what tells the two measurements apart, which a key of
    # coloured words cannot show, so the top row names its two marks.
    ft.label_points(ax, [raw[-1], adjusted[-1]], [rows[-1], rows[-1]],
                    ['raw', 'adjusted'])
    ax.set_xlabel('Gender pay gap (%)')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'dumbbell: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
