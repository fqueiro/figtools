"""Draw the README's example figure from synthetic data.

    uv run python docs/readme_figure.py

Writes docs/readme_figure.png beside this script and fails if any label
overlaps something or runs off the figure.
"""

from pathlib import Path

import numpy as np

import figtools as ft


def main():
    rng = np.random.default_rng(7)
    ft.setup()

    years = np.arange(2010, 2025)
    t = (years - 2010) / 14
    paths = {
        'Services': 100 + 34 * t + 4 * np.sin(5 * t),
        'Manufacturing': 100 + 8 * t - 10 * np.sin(3 * t) + 10 * t**2,
        'Construction': 100 - 22 * np.sin(2.6 * t) + 27 * t,
        'Agriculture': 100 - 14 * t + 3 * np.sin(6 * t),
    }

    fig, (left, right) = ft.panels(2, aspect=0.8, width=8.0)
    for name, y in paths.items():
        y = y + rng.normal(0, 0.8, len(years))
        left.plot(years, y, label=name)
        if name == 'Services':
            ft.band(left, years, y - 3 - 5 * t, y + 3 + 5 * t)
    left.set_ylabel('Employment (2010 = 100)')
    ft.label_lines(left)

    names = ['Schooling', 'Experience', 'Exporter', 'Firm age', 'Urban', 'Female']
    est = np.array([0.42, 0.18, 0.31, -0.06, 0.09, -0.15])
    se = np.array([0.06, 0.05, 0.09, 0.04, 0.07, 0.06])
    ft.dotplot(right, names, est, ci=(est - 1.96 * se, est + 1.96 * se))
    ft.refline(right, 0, 'no effect', axis='x')
    right.set_xlabel('Coefficient')

    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)
    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'readme_figure: labels collide: {found}')


if __name__ == '__main__':
    main()
