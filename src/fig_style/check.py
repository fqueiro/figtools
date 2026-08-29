"""Check every figure in a document for labels that overlap something.

Direct labelling fails silently: a label landing on a line, on a marker, or on
another label still renders and still raises nothing, so it survives until
somebody looks at the picture. This runs a document's code the way its
renderer would (the python chunks of a .qmd, the code cells of a .ipynb) and
measures each figure it produces with `fig_style.collisions`, which is quicker
than rendering and reading, and does not depend on noticing.

    uv run fig-check code
    uv run fig-check code adoption_lifecycle

With no document names it checks every .qmd and .ipynb in the directory.
Exits non-zero if anything overlaps, so it can gate a commit. `--figs DIR`
writes each figure to DIR as a png, which is how to see what a reported
overlap looks like.
"""

import argparse
import io
import json
import re
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from fig_style import collisions

CHUNK = re.compile(r'^```\{python\}\s*$(.*?)^```\s*$', re.MULTILINE | re.DOTALL)
FIGSIZE = re.compile(r'^\s*fig-(width|height):\s*([\d.]+)\s*$', re.MULTILINE)


def chunks(text):
    """The python chunks of a .qmd, in order, without their `#|` options."""
    for m in CHUNK.finditer(text):
        yield '\n'.join(ln for ln in m.group(1).splitlines()
                        if not ln.lstrip().startswith('#|'))


def cells(text):
    """The code cells of a .ipynb, in order, without magic and shell lines."""
    for cell in json.loads(text).get('cells', []):
        if cell.get('cell_type') != 'code':
            continue
        src = cell.get('source', [])
        body = ''.join(src) if isinstance(src, list) else src
        yield '\n'.join(ln for ln in body.splitlines()
                        if not ln.lstrip().startswith(('%', '!')))


def figsize(text):
    """The document's own figure size, so the check sees what Quarto renders.

    Crowding is measured as a fraction of the panel, so it depends on how big
    the panel is; checking at matplotlib's default size would be measuring a
    figure the reader never sees.
    """
    got = dict(FIGSIZE.findall(text))
    return float(got.get('width', 6.4)), float(got.get('height', 4.8))


def check(doc, figs=None):
    """Run one document and return its overlaps as printable lines."""
    text = doc.read_text(encoding='utf-8')
    is_qmd = doc.suffix == '.qmd'
    # A notebook carries no fig-width header; matplotlib's default size is
    # what its renderer shows.
    plt.rcParams['figure.figsize'] = figsize(text) if is_qmd else (6.4, 4.8)
    # The document's kernel supplies these; cells assume them without
    # importing.
    ns = {'__name__': '__main__', 'display': lambda *a, **k: None,
          'get_ipython': lambda: None}
    found, n = [], 0
    for i, body in enumerate(chunks(text) if is_qmd else cells(text)):
        with warnings.catch_warnings(), redirect_stdout(io.StringIO()):
            warnings.simplefilter('ignore')
            # Running the document's own code is the point: the figures only
            # exist once it has run. Same trust as rendering the document.
            try:
                exec(compile(body, f'{doc.name}:chunk{i}', 'exec'), ns)  # noqa: S102
            except AssertionError:
                # The inline `assert not collisions(fig)` idiom: the document
                # measured its own figure and found overlaps. Report it and
                # measure the open figure below for the specifics, rather
                # than letting one collision abort the whole batch. Any
                # other exception is a broken document and still raises.
                found.append(f'  chunk {i}  a collision assert failed')
        for num in plt.get_fignums():
            fig = plt.figure(num)
            if not fig.axes:
                plt.close(fig)
                continue
            n += 1
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                hits = collisions(fig)
                if figs is not None:
                    fig.savefig(figs / f'{doc.stem}_{n}.png', dpi=110)
            found += [f'  fig {n} panel {p}  {kind:12} {what}'
                      for p, kind, what in hits]
            plt.close(fig)
    return n, found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('docs', type=Path,
                    help='directory holding the documents (.qmd, .ipynb)')
    ap.add_argument('names', nargs='*', help='document stems; default all')
    ap.add_argument('--figs', type=Path, help='also write each figure here')
    a = ap.parse_args(argv)

    docs = a.docs.resolve()
    # The documents import their own domain modules (data loaders, semantic
    # colour maps) from the directory they live in.
    sys.path.insert(0, str(docs))

    if a.figs:
        a.figs.mkdir(parents=True, exist_ok=True)
    total = 0
    for doc in sorted([*docs.glob('*.qmd'), *docs.glob('*.ipynb')]):
        if a.names and doc.stem not in a.names:
            continue
        n, found = check(doc, a.figs)
        total += len(found)
        print(f'{doc.name:28} {n:2} figures  '
              f'{f"{len(found)} overlap(s)" if found else "clean"}')
        for line in found:
            print(line)
    print(f'\nTOTAL: {total} overlap(s)')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
