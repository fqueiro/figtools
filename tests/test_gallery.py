"""The gallery scripts run clean.

Each script in docs/gallery, and docs/readme_figure.py, draws a figure of a
kind the library is for and fails when `collisions` finds anything, so
running them tests label placement on realistic figures as well as keeping
the examples working. Each runs in a process of its own, as a reader would
run it, so it starts from matplotlib's defaults rather than from whatever
earlier tests left behind; and each runs from a copy in a temporary folder,
so the PNG it writes beside itself lands there and the tracked images stay
as they are.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / 'docs'
GALLERY = DOCS / 'gallery'
SCRIPTS = [*sorted(GALLERY.glob('*.py')), DOCS / 'readme_figure.py']


@pytest.fixture(scope='module')
def runs(tmp_path_factory):
    """Every script started at once, each from its own copy, with its output
    going to a log beside it; each test then waits on its own script. Run one
    after another they would take about as long as the rest of the suite.
    """
    folder = tmp_path_factory.mktemp('gallery')
    # No window backend: CI machines have no display, and nothing is shown.
    env = {**os.environ, 'MPLBACKEND': 'Agg'}
    started = {}
    for script in SCRIPTS:
        copy = folder / script.name
        shutil.copy(script, copy)
        with open(copy.with_suffix('.log'), 'w') as log:
            started[script.name] = (copy, subprocess.Popen(
                [sys.executable, str(copy)], cwd=folder, env=env,
                stdout=log, stderr=subprocess.STDOUT))
    yield started
    for _, proc in started.values():
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize('script', SCRIPTS, ids=lambda p: p.stem)
def test_script_draws_its_figure_with_nothing_colliding(script, runs):
    copy, proc = runs[script.name]
    code = proc.wait(timeout=300)
    assert code == 0, copy.with_suffix('.log').read_text()
    assert copy.with_suffix('.png').stat().st_size > 0


def test_every_gallery_script_has_its_image_and_every_image_its_script():
    """The images are what the README shows, so a script added without
    running it, or one renamed with its old image left behind, fails here."""
    scripts = {p.stem for p in GALLERY.glob('*.py')}
    images = {p.stem for p in GALLERY.glob('*.png')}
    assert scripts
    assert scripts == images
