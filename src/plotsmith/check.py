"""Check every figure in a document for labels that overlap something.

Direct labelling fails silently: a label landing on a line, on a marker, or on
another label still renders and still raises nothing, so it survives until
somebody looks at the picture. This runs a document's code the way its
renderer would (the `# %%` cells of a percent-format .py, the python chunks
of a .qmd, the code cells of a .ipynb) and measures each figure it produces
with `plotsmith.collisions`, which is quicker than rendering and reading, and
does not depend on noticing.

    uv run fig-check code
    uv run fig-check code adoption_lifecycle

With no document names it checks every document in the directory: each .qmd
and .ipynb, and each .py that Quarto would render, which is one with a
markdown or raw cell marker; a plain module has none and is left alone.
Exits non-zero if anything overlaps, so it can gate a commit. `--figs DIR`
writes each figure to DIR as a png, which is how to see what a reported
overlap looks like.
"""

import argparse
import io
import json
import re
import subprocess
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from plotsmith import collisions

CHUNK = re.compile(r'^```\{python\}\s*$(.*?)^```\s*$', re.MULTILINE | re.DOTALL)
FIGSIZE = re.compile(r'^\s*fig-(width|height):\s*([\d.]+)\s*$', re.MULTILINE)
# The percent-format rules below are Quarto's own (quarto-cli,
# src/core/jupyter/percent.ts), so a cell here is a cell there.
MARKER = re.compile(r'^\s*#\s*%%+\s*(?:\[(markdown|raw)\])?\s*(.*)?$')
PROSE = re.compile(r'^\s*#\s*%%+\s+\[(markdown|raw)\]', re.MULTILINE)
QUOTE = re.compile(r'^"{3,}\s*$')


def percent(text):
    """The cells of a percent-format .py as (kind, body), in order.

    A cell opens at a `# %%` line: alone (or with a title) for code, with
    `[markdown]` or `[raw]` for prose. Lines before the first marker are
    dropped. A prose cell is either one triple-quoted string or comment
    lines, and comes back without its comment prefix, so the front matter
    reads as YAML.
    """
    cells = []
    for line in text.strip().splitlines():
        m = MARKER.match(line)
        if m:
            cells.append((m.group(1) or 'code', []))
        elif cells:
            cells[-1][1].append(line)
    for kind, rows in cells:
        while rows and not rows[0].strip():
            rows.pop(0)
        while rows and not rows[-1].strip():
            rows.pop()
        if kind != 'code':
            if len(rows) > 2 and QUOTE.match(rows[0]) and QUOTE.match(rows[-1]):
                rows = rows[1:-1]
            else:
                rows = [re.sub(r'^#\s?', '', row) for row in rows]
        yield kind, '\n'.join(rows)


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


def parse(doc):
    """A document's prose, where its front matter lives, and its code cells.

    Each format is read the way its renderer reads it. A notebook carries no
    front matter, so its prose is empty and the figure size falls to
    matplotlib's default, which is what its renderer shows.
    """
    text = doc.read_text(encoding='utf-8')
    if doc.suffix == '.qmd':
        return text, list(chunks(text))
    if doc.suffix == '.ipynb':
        return '', list(cells(text))
    if doc.suffix == '.py':
        parts = list(percent(text))
        return ('\n'.join(body for kind, body in parts if kind != 'code'),
                [body for kind, body in parts if kind == 'code'])
    raise ValueError(f'{doc.name}: not a format fig-check reads')


def documents(folder):
    """The documents in a folder, sorted.

    Every .qmd and .ipynb, and each .py Quarto would render: one with a
    markdown or raw cell marker. A plain module has none and is left alone,
    which matters because checking a document means running it.
    """
    found = [*folder.glob('*.qmd'), *folder.glob('*.ipynb')]
    found += [doc for doc in folder.glob('*.py')
              if PROSE.search(doc.read_text(encoding='utf-8'))]
    return sorted(found)


def check(doc, figs=None):
    """Run one document and return its overlaps as printable lines."""
    prose, bodies = parse(doc)
    plt.rcParams['figure.figsize'] = figsize(prose)
    # The document's kernel supplies these; cells assume them without
    # importing.
    ns = {'__name__': '__main__', 'display': lambda *a, **k: None,
          'get_ipython': lambda: None}
    found, n = [], 0
    for i, body in enumerate(bodies):
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
                    help='directory holding the documents (.py, .qmd, .ipynb)')
    ap.add_argument('names', nargs='*', help='document stems; default all')
    ap.add_argument('--figs', type=Path, help='also write each figure here')
    ap.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    docs = a.docs.resolve()
    # The documents import their own domain modules (data loaders, semantic
    # colour maps) from the directory they live in.
    sys.path.insert(0, str(docs))
    targets = [doc for doc in documents(docs)
               if not a.names or doc.stem in a.names]

    if a.figs:
        a.figs.mkdir(parents=True, exist_ok=True)

    if a.worker:
        total = 0
        for doc in targets:
            n, found = check(doc, a.figs)
            total += len(found)
            print(f'{doc.name:28} {n:2} figures  '
                  f'{f"{len(found)} overlap(s)" if found else "clean"}',
                  flush=True)
            for line in found:
                print(line, flush=True)
        return 1 if total else 0

    # One subprocess per document: a document's own renderer gives it a
    # fresh interpreter, and a batch of data-heavy documents run in one
    # process accumulates into a MemoryError none of them hits alone.
    bad = 0
    for doc in targets:
        cmd = [sys.executable, '-m', 'plotsmith.check', '--worker',
               str(docs), doc.stem]
        if a.figs:
            cmd += ['--figs', str(a.figs)]
        rc = subprocess.run(cmd).returncode
        if rc not in (0, 1):
            # The document itself crashed; surface that, don't count it.
            return rc
        bad += rc
    print(f'\nTOTAL: {bad} document(s) with overlaps')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
