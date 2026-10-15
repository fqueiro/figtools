"""Coefficient plot: one estimate per row with its interval, ranked, and a
named zero rule, from synthetic data.

    uv run python docs/gallery/coefficient_plot.py

Writes coefficient_plot.png beside this script and fails if any label
overlaps something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()

    # The wage premium of exporting firms, estimated industry by industry.
    industries = ['Textiles', 'Food', 'Wood and paper', 'Chemicals',
                  'Metals', 'Machinery', 'Electronics', 'Vehicles']
    est = np.array([1.2, 3.5, 2.8, 9.4, 6.1, 8.3, 12.8, 7.1])
    se = np.array([1.6, 1.2, 1.9, 2.1, 1.4, 1.7, 2.6, 2.0])

    fig, ax = plt.subplots(figsize=(5.9, 3.4))
    ft.dotplot(ax, industries, est, ci=(est - 1.96 * se, est + 1.96 * se))
    ft.refline(ax, 0, 'no premium', axis='x')
    ax.set_xlabel('Exporter wage premium (%)')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'coefficient_plot: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
