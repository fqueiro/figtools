"""Binned scatter: mean wages within twenty quantile bins of firm size,
net of a control, with the fitted slope, from synthetic data.

    uv run python docs/gallery/binned_scatter.py

Writes binned_scatter.png beside this script and fails if any label
overlaps something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(5)

    # Twenty thousand workers. Older workers sit in larger firms and earn
    # more, so the raw size gradient overstates the one at a given age.
    n = 20_000
    age = rng.uniform(20, 65, n)
    size = np.clip(rng.normal(2.2 + 0.03 * (age - 40), 1.4, n), 0, None)
    wage = (1.9 + 0.06 * size - 0.003 * size ** 2 + 0.012 * (age - 40)
            + rng.normal(0, 0.35, n))

    fig, ax = plt.subplots(figsize=(5.9, 3.6))
    ft.binscatter(ax, size, wage, controls=age)
    ax.set_xlabel('Firm size (log employees), net of age')
    ax.set_ylabel('Log hourly wage, net of age')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'binned_scatter: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
