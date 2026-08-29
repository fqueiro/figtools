"""Check every figure in a document for labels that overlap something.

Direct labelling fails silently: a label landing on a line, on a marker, or on
another label still renders and still raises nothing, so it survives until
somebody looks at the picture. This runs a document's python chunks the way
Quarto does and measures each figure it produces with `fig_style.collisions`,
which is quicker than rendering and reading, and does not depend on noticing.

    uv run fig-check code
    uv run fig-check code adoption_lifecycle

With no document names it checks every .qmd in the directory. Exits non-zero
if anything overlaps, so it can gate a commit. `--figs DIR` writes each figure
to DIR as a png, which is how to see what a reported overlap looks like.
"""

import argparse
import io
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


def figsize(text):
    """The document's own figure size, so the check sees what Quarto renders.

    Crowding is measured as a fraction of the panel, so it depends on how big
    the panel is; checking at matplotlib's default size would be measuring a
    figure the reader never sees.
    """
    got = dict(FIGSIZE.findall(text))
    return float(got.get('width', 6.4)), float(got.get('height', 4.8))


def check(qmd, figs=None):
    """Run one document and return its overlaps as printable lines."""
    text = qmd.read_text(encoding='utf-8')
    plt.rcParams['figure.figsize'] = figsize(text)
    # Quarto's kernel supplies these; chunks assume them without importing.
    ns = {'__name__': '__main__', 'display': lambda *a, **k: None,
          'get_ipython': lambda: None}
    found, n = [], 0
    for i, body in enumerate(chunks(text)):
        with warnings.catch_warnings(), redirect_stdout(io.StringIO()):
            warnings.simplefilter('ignore')
            # Running the document's own code is the point: the figures only
            # exist once it has run. Same trust as rendering the document.
            exec(compile(body, f'{qmd.name}:chunk{i}', 'exec'), ns)  # noqa: S102
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
                    fig.savefig(figs / f'{qmd.stem}_{n}.png', dpi=110)
            found += [f'  fig {n} panel {p}  {kind:12} {what}'
                      for p, kind, what in hits]
            plt.close(fig)
    return n, found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('docs', type=Path, help='directory holding the .qmd files')
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
    for qmd in sorted(docs.glob('*.qmd')):
        if a.names and qmd.stem not in a.names:
            continue
        n, found = check(qmd, a.figs)
        total += len(found)
        print(f'{qmd.name:28} {n:2} figures  '
              f'{f"{len(found)} overlap(s)" if found else "clean"}')
        for line in found:
            print(line)
    print(f'\nTOTAL: {total} overlap(s)')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
