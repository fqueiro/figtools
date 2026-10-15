"""Small multiples: one panel per region, the same three series in each,
named once by a key in the first panel, from synthetic data.

    uv run python docs/gallery/small_multiples.py

Writes small_multiples.png beside this script and fails if any label
overlaps something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(8)

    # Unemployment by age group in four regions, through one recession.
    years = np.arange(2005, 2025)
    slump = np.exp(-((years - 2012) / 3.0) ** 2)
    groups = {'Under 25': (14.0, 16.0), '25 to 54': (6.0, 6.0),
              '55 and over': (5.0, 4.0)}    # level, and its rise in the slump
    regions = {'North': 0.8, 'Centre': 1.0, 'South': 1.3, 'Islands': 1.5}

    fig, axes = plt.subplots(2, 2, figsize=(5.9, 4.2), sharex=True,
                             sharey=True)
    for ax, (region, scale) in zip(axes.flat, regions.items()):
        for name, (level, rise) in groups.items():
            noise = rng.normal(0, 0.4, len(years))
            ax.plot(years, scale * (level + rise * slump) + noise, label=name)
        ax.set_title(region)
    # Each panel restarts the colour cycle, so a series has the same colour
    # in every panel and one key serves them all. The upper left is free
    # too, but there the key would read as part of the panel's title.
    ft.key(axes[0, 0], [(name, ft.PALETTE[i]) for i, name in enumerate(groups)],
           loc='upper right')
    fig.supylabel('Unemployment rate (%)')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'small_multiples: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
