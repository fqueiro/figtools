"""A shaded period named in place, beside series labelled at their ends,
from synthetic data.

    uv run python docs/gallery/shaded_period.py

Writes shaded_period.png beside this script and fails if any label overlaps
something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(4)

    # Employment by firm size through a recession: small firms lose the
    # most jobs and take the longest to win them back.
    years = np.arange(2000, 2025)
    falling = np.clip((years - 2008) / 5, 0, 1)    # the fall, 2008 to 2013
    since = np.clip(years - 2013, 0, None)         # years of recovery
    sizes = {    # trend growth a year, depth of the fall, recovery time
        'Small firms': (0.8, 12, 6.0),
        'Medium firms': (0.9, 7, 4.0),
        'Large firms': (0.6, 4, 2.5),
    }

    fig, ax = plt.subplots(figsize=(5.9, 3.4))
    for name, (trend, depth, recovery) in sizes.items():
        path = (100 + trend * (years - 2000)
                - depth * falling * np.exp(-since / recovery))
        ax.plot(years, path + rng.normal(0, 0.5, len(years)), label=name)
    ft.shade(ax, 2008, 2013, 'recession')
    ax.set_ylabel('Employment (2000 = 100)')
    ft.label_lines(ax)

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'shaded_period: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
