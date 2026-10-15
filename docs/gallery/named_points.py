"""Scatter with a few points named: each name placed clear of the other
names and of every point, from synthetic data.

    uv run python docs/gallery/named_points.py

Writes named_points.png beside this script and fails if any label overlaps
something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(2)

    # Forty regions, wages rising with the share of graduates, and six
    # regions the text discusses: five far off the trend, one in the thick
    # of it.
    degree = rng.uniform(12, 34, 40)
    wage = 7.0 + 0.2 * degree + rng.normal(0, 0.7, 40)
    named = {'Capital region': (47.0, 18.2), 'University town': (39.0, 12.1),
             'Port city': (23.0, 14.6), 'Mining district': (13.0, 13.1),
             'Tourist coast': (18.0, 8.6), 'Industrial belt': (26.0, 12.4)}
    x, y = zip(*named.values())

    fig, ax = plt.subplots(figsize=(5.9, 3.6))
    ax.scatter([*degree, *x], [*wage, *y], s=5.5 ** 2)
    ft.label_points(ax, x, y, list(named))
    ax.set_xlabel('Workers with a degree (%)')
    ax.set_ylabel('Median hourly wage (€)')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'named_points: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
