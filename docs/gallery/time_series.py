"""Time series with direct labels, confidence bands and a named reference
rule, from synthetic data.

    uv run python docs/gallery/time_series.py

Writes time_series.png beside this script and fails if any label overlaps
something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(3)

    # Real hourly wages by education, indexed to the first year, as a survey
    # would estimate them: each year's figure carries sampling error.
    years = np.arange(2005, 2025)
    t = years - 2005
    groups = {
        'Tertiary': 100 + 1.4 * t - 6 * np.exp(-((t - 8) / 2.5) ** 2),
        'Upper secondary': 100 + 0.6 * t - 5 * np.exp(-((t - 8) / 3) ** 2),
        'Lower secondary': 100 - 0.2 * t - 4 * np.exp(-((t - 9) / 3) ** 2),
    }

    fig, ax = plt.subplots(figsize=(5.9, 3.4))
    for name, path in groups.items():
        y = path + rng.normal(0, 0.6, len(years))
        se = 0.8 + 0.06 * t
        ax.plot(years, y, label=name)
        ft.band(ax, years, y - 1.96 * se, y + 1.96 * se)
    ft.refline(ax, 100, '2005 level')
    ax.set_ylabel('Real hourly wage (2005 = 100)')
    ft.label_lines(ax)

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'time_series: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
