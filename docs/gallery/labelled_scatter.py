"""Labelled scatter: each industry's name printed as its mark, with the OLS
fit and a note giving N and R², from synthetic data.

    uv run python docs/gallery/labelled_scatter.py

Writes labelled_scatter.png beside this script and fails if any label
overlaps something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt

import figtools as ft


def main():
    ft.setup()

    # Robot adoption and productivity growth across manufacturing industries.
    industries = {
        'Apparel': (0.6, 0.4), 'Printing': (1.1, 0.9), 'Textiles': (1.8, 0.6),
        'Wood': (2.4, 1.5), 'Furniture': (3.0, 1.0), 'Paper': (3.8, 1.8),
        'Food': (4.5, 1.3), 'Beverages': (5.2, 2.0), 'Glass': (6.0, 1.6),
        'Pharmaceuticals': (6.6, 3.3), 'Chemicals': (7.3, 2.5),
        'Machinery': (8.2, 2.9), 'Plastics': (9.0, 2.2), 'Metals': (10.1, 2.7),
        'Electronics': (11.2, 3.9), 'Vehicles': (12.6, 3.4),
    }
    robots, growth = zip(*industries.values())

    fig, ax = plt.subplots(figsize=(5.9, 3.6))
    # Each name is centred on its point, so the panel needs more room at
    # the sides than autoscale's padding gives a dot.
    ax.margins(x=0.08)
    ft.label_scatter(ax, robots, growth, list(industries), fit=True, note=True)
    ax.set_xlabel('Robots per 1,000 workers')
    ax.set_ylabel('Labour productivity growth (% a year)')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'labelled_scatter: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
