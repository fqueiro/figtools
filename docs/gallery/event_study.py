"""Event study: estimates with their intervals, a zero rule and a named
treatment date, from synthetic data.

    uv run python docs/gallery/event_study.py

Writes event_study.png beside this script and fails if any label overlaps
something or runs off the figure.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import figtools as ft


def main():
    ft.setup()
    rng = np.random.default_rng(11)

    # Effect of a payroll-tax cut on log employment, by year relative to the
    # cut. The year before it is the reference, so its estimate is zero.
    k = np.arange(-5, 7)
    effect = np.where(k < 0, 0.0, 0.045 * (1 - np.exp(-(k + 1) / 2.0)))
    est = effect + rng.normal(0, 0.004, len(k))
    se = 0.006 + 0.001 * np.abs(k + 1)
    est[k == -1], se[k == -1] = 0.0, 0.0

    fig, ax = plt.subplots(figsize=(5.9, 3.2))
    ft.pointrange(ax, k, est, est - 1.96 * se, est + 1.96 * se)
    ft.refline(ax, 0)
    ft.refline(ax, -0.5, 'tax cut', axis='x')
    ax.set_xlabel('Years since the tax cut')
    ax.set_ylabel('Effect on log employment')

    found = ft.collisions(fig)
    if found:
        raise SystemExit(f'event_study: labels collide: {found}')
    fig.savefig(Path(__file__).with_suffix('.png'), dpi=150)


if __name__ == '__main__':
    main()
