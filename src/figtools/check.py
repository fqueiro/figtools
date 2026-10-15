"""Check every figure in a document for labels that overlap something.

Direct labelling fails silently: a label landing on a line, on a marker, or on
another label still renders and still raises nothing, so it survives until
somebody looks at the picture. This runs a document's code the way its
renderer would (the `# %%` cells of a percent-format .py, the python chunks
of a .qmd, the code cells of a .ipynb) and measures each figure it produces
with `figtools.collisions`, which is quicker than rendering and reading, and
does not depend on noticing.

    uv run fig-check code
    uv run fig-check code adoption_lifecycle

With no document names it checks every document in the directory: each .qmd
and .ipynb, and each .py that Quarto would render, which is one with a
markdown or raw cell marker; a plain module has none and is left alone. A
name that matches no document is an error, as is a directory without any.
Each figure is drawn at the size the html render gives it: `fig-width` and
`fig-height` from the document's front matter, the project's `_quarto.yml`
and any `_metadata.yml`, merged as Quarto merges them, else Quarto's 7 by 5.
Each document runs in the folder Quarto runs it in, its own unless the
project's `_quarto.yml` sets `execute-dir: project`, so a path it reads
relative to that folder resolves wherever the check is called from.
Exits non-zero if anything overlaps, so it can gate a commit. `--figs DIR`
writes each figure to DIR as a png, which is how to see what a reported
overlap looks like.
"""

import argparse
import io
import json
import linecache
import os
import re
import subprocess
import sys
import warnings
from contextlib import redirect_stdout
from pathlib import Path

import matplotlib
import yaml

matplotlib.use('Agg')
import matplotlib.pyplot as plt

from figtools import collisions
from figtools.digest import KEY, describe, encode

CHUNK = re.compile(r'^```\{python\}\s*$(.*?)^```\s*$', re.MULTILINE | re.DOTALL)
FRONT = re.compile(r'\A\s*---\s*\n(.*?)\n---\s*$', re.DOTALL | re.MULTILINE)
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


def source(cell):
    """A notebook cell's text; the file stores it as lines or one string."""
    src = cell.get('source', [])
    return ''.join(src) if isinstance(src, list) else src


def cells(notebook):
    """The code cells of a .ipynb, in order, without magic and shell lines."""
    for cell in notebook.get('cells', []):
        if cell.get('cell_type') == 'code':
            yield '\n'.join(ln for ln in source(cell).splitlines()
                            if not ln.lstrip().startswith(('%', '!')))


def mapping(loaded, where):
    """A parsed YAML document as a dict, or a clear error naming its file."""
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise TypeError(f'{where}: expected a YAML mapping')
    return loaded


def front_matter(prose, where):
    """The YAML block a document's prose opens with; {} without one."""
    m = FRONT.match(prose)
    return mapping(yaml.safe_load(m.group(1)), where) if m else {}


def settings(folder):
    """Quarto's directory and project settings for documents in a folder.

    Every `_metadata.yml` from the folder upward, nearest first, then the
    `_quarto.yml` that ends the walk; nothing when the folder sits in no
    project.
    """
    layers = []
    for here in (folder, *folder.parents):
        for name in ('_metadata.yml', '_quarto.yml', '_quarto.yaml'):
            path = here / name
            if path.exists():
                loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
                layers.append(mapping(loaded, path))
                if name != '_metadata.yml':
                    return layers
    return layers


def figsize(doc, prose):
    """The figure size the html render gives a document's figures, in inches.

    Quarto sets matplotlib's figure size from `fig-width` and `fig-height`,
    merged the way it merges metadata: the project's `_quarto.yml`, then
    each `_metadata.yml` down to the document's folder, then the document's
    front matter, a later file overriding an earlier one key by key, and a
    key under `format: html:` overriding the same key at the top level
    wherever either sits; 7 by 5 when nothing sets them. Crowding is
    measured as a fraction of the panel, so any other size would be
    measuring a figure the reader never sees.
    """
    top, html = {}, {}
    for meta in (*reversed(settings(doc.parent)), front_matter(prose, doc)):
        top.update((k, v) for k, v in meta.items() if k.startswith('fig-'))
        fmt = meta.get('format')
        if isinstance(fmt, dict) and isinstance(fmt.get('html'), dict):
            html.update((k, v) for k, v in fmt['html'].items()
                        if k.startswith('fig-'))
    got = {**top, **html}
    return float(got.get('fig-width', 7)), float(got.get('fig-height', 5))


def workdir(doc):
    """The folder Quarto runs a document's code in.

    The document's own folder, Quarto's default, unless the `_quarto.yml`
    of the project it sits in sets `execute-dir: project`, which runs it
    in the project's folder. A document that reads its data relative to
    the working folder finds it only there.
    """
    for here in (doc.parent, *doc.parent.parents):
        for name in ('_quarto.yml', '_quarto.yaml'):
            path = here / name
            if path.exists():
                loaded = mapping(yaml.safe_load(path.read_text(encoding='utf-8')), path)
                project = loaded.get('project') or {}
                return here if project.get('execute-dir') == 'project' else doc.parent
    return doc.parent


def parse(doc):
    """A document's prose, where its front matter lives, and its code cells.

    Each format is read the way its renderer reads it; a notebook's front
    matter is a raw cell at the top, which is where Quarto looks for it.
    """
    text = doc.read_text(encoding='utf-8')
    if doc.suffix == '.qmd':
        return text, list(chunks(text))
    if doc.suffix == '.ipynb':
        notebook = json.loads(text)
        first = (notebook.get('cells') or [{}])[0]
        prose = source(first) if first.get('cell_type') == 'raw' else ''
        return prose, list(cells(notebook))
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
    plt.rcParams['figure.figsize'] = figsize(doc, prose)
    # The document's kernel supplies these; cells assume them without
    # importing.
    ns = {'__name__': '__main__', 'display': lambda *a, **k: None,
          'get_ipython': lambda: None}
    caller = Path.cwd()
    os.chdir(workdir(doc))
    try:
        return run(doc, bodies, ns, figs)
    finally:
        os.chdir(caller)


def run(doc, bodies, ns, figs):
    """Execute a document's cells in `ns`, measuring each figure as it closes."""
    found, n = [], 0
    for i, body in enumerate(bodies):
        with warnings.catch_warnings(), redirect_stdout(io.StringIO()):
            warnings.simplefilter('ignore')
            # Running the document's own code is the point: the figures only
            # exist once it has run. Same trust as rendering the document.
            # A kernel registers each cell's source under the cell's name,
            # which is where `inspect` reads a function's source from; code
            # keyed on that source (a document's cached steps) fails without.
            name = f'<{doc.name}:chunk{i}>'
            linecache.cache[name] = (len(body), None, body.splitlines(True), name)
            try:
                exec(compile(body, name, 'exec'), ns)  # noqa: S102
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
                    # The same digest a rendered page's image carries, so
                    # `render-digest figure` reads these too.
                    fig.savefig(figs / f'{doc.stem}_{n}.png', dpi=110,
                                metadata={KEY: encode(describe(fig))})
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
    if not docs.is_dir():
        ap.error(f'{docs} is not a directory')
    found = documents(docs)
    if not found:
        ap.error(f'no documents in {docs} (a .py with a markdown cell, '
                 'a .qmd, or a .ipynb)')
    missing = sorted(set(a.names) - {doc.stem for doc in found})
    if missing:
        ap.error(f'no document named {", ".join(missing)} in {docs}; '
                 f'there: {", ".join(doc.name for doc in found)}')
    # The documents import their own domain modules (data loaders, semantic
    # colour maps) from the directory they live in.
    sys.path.insert(0, str(docs))
    targets = [doc for doc in found if not a.names or doc.stem in a.names]

    if a.figs:
        # Absolute, since each document runs from its own working folder.
        a.figs = a.figs.resolve()
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
        cmd = [sys.executable, '-m', 'figtools.check', '--worker',
               str(docs), doc.stem]
        if a.figs:
            cmd += ['--figs', str(a.figs)]
        rc = subprocess.run(cmd, check=False).returncode
        if rc not in (0, 1):
            # The document itself crashed; surface that, don't count it.
            return rc
        bad += rc
    print(f'\nTOTAL: {bad} document(s) with overlaps')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
