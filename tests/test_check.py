"""fig-check runs documents of every format and measures their figures."""

import json
import subprocess
import sys

import matplotlib
import pytest

matplotlib.use('Agg')
import matplotlib.pyplot as plt

import figtools.check as fc


@pytest.fixture(autouse=True)
def _restore_rcparams():
    # The check sets the figure size globally, as Quarto's own setup cell
    # does; the next test, in this file or another, starts from defaults.
    with matplotlib.rc_context():
        yield


# Both prose forms Quarto accepts, a title on a code marker, a `#|` option,
# and a figure drawn under the main guard, which a render satisfies.
PY = '''# %% [markdown]
# ---
# title: Percent script
# fig-width: 5
# ---

# %% [markdown]
"""
## Prose as a string

fig-width: 9 here is prose, not front matter, and must not run.
"""

# %% setup
#| echo: false
import matplotlib.pyplot as plt
import figtools as ft
ft.setup()

# %%
if __name__ == '__main__':
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='a')
    ft.label_lines(ax)
'''

QMD = '''---
fig-width: 5
---

```{python}
import matplotlib.pyplot as plt
import figtools as ft
ft.setup()
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label='a')
ft.label_lines(ax)
```
'''


def test_check_runs_a_qmd_and_honours_its_figure_size(tmp_path):
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD, encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []
    # Width from the front matter, height from Quarto's html default.
    assert tuple(plt.rcParams['figure.figsize']) == (5.0, 5.0)


def test_figsize_merges_project_and_document_the_way_quarto_does(tmp_path):
    (tmp_path / '_quarto.yml').write_text(
        'fig-height: 3\nformat:\n  html:\n    fig-width: 9\n', encoding='utf-8')
    doc = tmp_path / 'code' / 'doc.py'
    doc.parent.mkdir()
    # The project's html-specific width beats the document's top-level one;
    # the height comes from the project alone.
    doc.write_text(PY, encoding='utf-8')
    assert fc.figsize(doc, fc.parse(doc)[0]) == (9.0, 3.0)
    # A document's html-specific width beats the project's.
    doc.write_text(PY.replace('# fig-width: 5',
                              '# format:\n#   html:\n#     fig-width: 5'),
                   encoding='utf-8')
    assert fc.figsize(doc, fc.parse(doc)[0]) == (5.0, 3.0)
    # A folder's _metadata.yml sits between the two.
    (doc.parent / '_metadata.yml').write_text('fig-height: 4\n',
                                              encoding='utf-8')
    assert fc.figsize(doc, fc.parse(doc)[0]) == (5.0, 4.0)


def test_figsize_reads_front_matter_from_a_raw_string_cell(tmp_path):
    """jupytext opens a prose cell with r\"\"\" when it holds a backslash,
    so a document's front matter can sit in a raw string."""
    doc = tmp_path / 'doc.py'
    doc.write_text('# %% [markdown]\nr"""\n---\ntitle: Raw\nfig-width: 6\n---\n'
                   'Inline $\\beta$.\n"""\n\n# %%\nx = 1\n', encoding='utf-8')
    prose = fc.parse(doc)[0]
    assert prose.startswith('---\ntitle: Raw')
    assert fc.figsize(doc, prose) == (6.0, 5.0)


def test_figsize_falls_to_quartos_html_default(tmp_path):
    assert fc.figsize(tmp_path / 'doc.qmd', '') == (7.0, 5.0)


def test_check_reports_a_failed_inline_assert_instead_of_dying(tmp_path):
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD.replace('ft.label_lines(ax)',
                               'ft.label_lines(ax)\nassert False'),
                   encoding='utf-8')
    _, found = fc.check(doc)
    assert any('assert' in line for line in found)


def test_check_lets_a_cell_read_the_source_of_its_functions(tmp_path):
    """A kernel registers each cell's source, so `inspect` reads a function
    defined in a cell, which is what a document's cached steps key on. Run
    from a string with no source registered, the same cell got an error or,
    worse, lines of whatever file was running at that line number."""
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD.replace('ft.setup()', 'ft.setup()\nimport inspect\n'
                               'def step():\n    return 1\n'
                               "if inspect.getsource(step) != 'def step():\\n    return 1\\n':\n"
                               "    raise RuntimeError('read the wrong source')"),
                   encoding='utf-8')
    n, found = fc.check(doc)
    assert (n, found) == (1, [])


READS_DATA = QMD.replace('ft.setup()', "ft.setup()\nopen('data.txt').read()")


def test_check_runs_a_document_in_its_own_folder(tmp_path, monkeypatch):
    """Quarto runs a document in its own folder, so a document reads its data
    relative to it; called from the project root, the check must too."""
    (tmp_path / '_quarto.yml').write_text('project:\n  type: default\n', encoding='utf-8')
    doc = tmp_path / 'code' / 'notes' / 'doc.qmd'
    doc.parent.mkdir(parents=True)
    doc.write_text(READS_DATA, encoding='utf-8')
    (doc.parent / 'data.txt').write_text('x', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    assert fc.check(doc) == (1, [])
    assert fc.workdir(doc) == doc.parent
    assert fc.Path.cwd() == tmp_path      # the caller's folder comes back


def test_check_runs_a_document_in_the_project_folder_when_quarto_does(tmp_path, monkeypatch):
    (tmp_path / '_quarto.yml').write_text('project:\n  execute-dir: project\n', encoding='utf-8')
    doc = tmp_path / 'code' / 'doc.qmd'
    doc.parent.mkdir()
    doc.write_text(READS_DATA, encoding='utf-8')
    (tmp_path / 'data.txt').write_text('x', encoding='utf-8')
    monkeypatch.chdir(doc.parent)
    assert fc.workdir(doc) == tmp_path
    assert fc.check(doc) == (1, [])
    assert fc.Path.cwd() == doc.parent


def test_percent_splits_cells_the_way_quarto_does():
    parts = list(fc.percent('import os\n' + PY))
    assert [kind for kind, _ in parts] == ['markdown', 'markdown', 'code',
                                           'code']
    assert parts[0][1] == '---\ntitle: Percent script\nfig-width: 5\n---'
    assert parts[1][1].startswith('## Prose as a string')
    assert parts[2][1].startswith('#| echo: false\nimport matplotlib')


def test_check_runs_a_percent_script_and_honours_its_front_matter(tmp_path):
    doc = tmp_path / 'doc.py'
    doc.write_text(PY, encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []
    assert tuple(plt.rcParams['figure.figsize']) == (5.0, 5.0)


def test_main_refuses_a_name_that_matches_no_document(tmp_path, capsys):
    (tmp_path / 'doc.py').write_text(PY, encoding='utf-8')
    with pytest.raises(SystemExit) as died:
        fc.main([str(tmp_path), 'doc', 'nope'])
    assert died.value.code == 2
    assert 'nope' in capsys.readouterr().err


def test_main_refuses_a_folder_without_documents(tmp_path):
    (tmp_path / 'lib.py').write_text('x = 1\n', encoding='utf-8')
    with pytest.raises(SystemExit) as died:
        fc.main([str(tmp_path)])
    assert died.value.code == 2


def test_documents_leaves_plain_modules_alone(tmp_path):
    (tmp_path / 'doc.py').write_text(PY, encoding='utf-8')
    (tmp_path / 'doc.qmd').write_text(QMD, encoding='utf-8')
    # A module with a bare `# %%` code marker is still not a document:
    # Quarto renders a .py only when it has a markdown or raw cell.
    (tmp_path / 'lib.py').write_text('# %%\nraise SystemExit("ran a module")\n',
                                     encoding='utf-8')
    assert [d.name for d in fc.documents(tmp_path)] == ['doc.py', 'doc.qmd']


def test_check_runs_an_ipynb_and_skips_magics(tmp_path):
    nb = {'cells': [
        {'cell_type': 'markdown', 'source': ['# not code\n']},
        {'cell_type': 'code', 'source': [
            '%config InlineBackend.figure_format = "retina"\n',
            'import matplotlib.pyplot as plt\n',
            'import figtools as ft\n',
            'ft.setup()\n',
            'fig, ax = plt.subplots()\n',
            "ax.plot([0, 1], [0, 1], label='a')\n",
            'ft.label_lines(ax)\n',
        ]},
    ], 'nbformat': 4}
    doc = tmp_path / 'doc.ipynb'
    doc.write_text(json.dumps(nb), encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []


# A document that opens a figure and then fails on an import, and one whose
# inline collision assert fails, which counts as overlaps.
BROKEN = '''# %% [markdown]
# ---
# title: Broken
# ---

# %%
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
import a_module_nobody_installed
'''
OVERLAPPING = QMD.replace('ft.label_lines(ax)', 'ft.label_lines(ax)\nassert False')


def folder(tmp_path, **docs):
    """Write documents into tmp_path, `name_ext=text` naming `name.ext`."""
    for name, text in docs.items():
        (tmp_path / name.replace('_', '.')).write_text(text, encoding='utf-8')
    return tmp_path


def lines(out):
    """A worker's status lines, keyed by the document they open with."""
    return {line.split()[0]: line for line in out.splitlines()
            if line and not line.startswith(' ')}


@pytest.fixture
def _keep_sys_path(monkeypatch):
    # main puts the documents' folder on the import path, as a worker needs.
    monkeypatch.setattr(sys, 'path', list(sys.path))


@pytest.mark.usefixtures('_keep_sys_path')
def test_worker_reports_a_crash_and_checks_the_documents_after_it(tmp_path, capsys):
    """Python exits 1 on an uncaught exception, the code for overlaps, so a
    broken document passed for one with overlaps and stopped the batch."""
    docs = folder(tmp_path, broken_py=BROKEN, clean_py=PY,
                  overlapping_qmd=OVERLAPPING)
    assert fc.main([str(docs), '--worker']) == fc.CRASHED
    out, err = capsys.readouterr()
    assert 'a_module_nobody_installed' in err      # the traceback
    status = lines(out)
    assert status['broken.py'].split()[1:] == ['crashed']
    # The figure the broken document left open is not counted as the next
    # document's.
    assert status['clean.py'].split()[1:] == ['1', 'figures', 'clean']
    assert 'overlap(s)' in status['overlapping.qmd']
    assert plt.get_fignums() == []


@pytest.mark.usefixtures('_keep_sys_path')
def test_worker_exit_code_says_what_it_found(tmp_path):
    docs = folder(tmp_path, clean_py=PY, overlapping_qmd=OVERLAPPING,
                  exits_py=PY.replace('ft.setup()', 'ft.setup()\nimport sys\nsys.exit()'))
    assert fc.main([str(docs), 'clean', '--worker']) == fc.CLEAN
    assert fc.main([str(docs), 'clean', 'overlapping', '--worker']) == fc.OVERLAPS
    # A cell calling sys.exit() fails a render, and must not pass as clean.
    assert fc.main([str(docs), 'exits', '--worker']) == fc.CRASHED


def test_main_names_crashed_documents_apart_from_overlaps(tmp_path, capfd):
    """End to end, one subprocess per document, as the command runs."""
    docs = folder(tmp_path, broken_py=BROKEN, clean_py=PY,
                  overlapping_qmd=OVERLAPPING)
    assert fc.main([str(docs)]) == fc.CRASHED
    out = capfd.readouterr().out
    assert 'TOTAL: 3 document(s), 1 with overlaps, 1 crashed' in out
    assert '  overlaps: overlapping.qmd' in out
    assert '  crashed:  broken.py' in out


def test_main_ranks_a_crash_above_overlaps_above_clean(tmp_path, monkeypatch, capsys):
    """Any code but 0 and 1 is a crash: a dying interpreter (an access
    violation, a kill) never reaches the worker's own handler."""
    docs = folder(tmp_path, a_qmd=QMD, b_qmd=QMD, c_qmd=QMD)
    codes, ran = {}, []

    def fake(cmd, check):
        ran.append(cmd[5])
        return subprocess.CompletedProcess(cmd, codes[cmd[5]])

    monkeypatch.setattr(fc.subprocess, 'run', fake)
    codes.update({'a.qmd': 0, 'b.qmd': 1, 'c.qmd': 3221225477})
    assert fc.main([str(docs)]) == fc.CRASHED
    assert ran == ['a.qmd', 'b.qmd', 'c.qmd']      # all of them, in order
    assert '  crashed:  c.qmd' in capsys.readouterr().out
    assert fc.main([str(docs), 'a', 'b']) == fc.OVERLAPS
    assert fc.main([str(docs), 'a']) == fc.CLEAN


def test_main_runs_each_of_two_documents_sharing_a_stem_once(tmp_path, monkeypatch):
    docs = folder(tmp_path, doc_py=PY, doc_qmd=QMD)
    ran = []

    def fake(cmd, check):
        ran.append(cmd[5])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fc.subprocess, 'run', fake)
    assert fc.main([str(docs), 'doc']) == fc.CLEAN
    assert ran == ['doc.py', 'doc.qmd']
    ran.clear()
    assert fc.main([str(docs), 'doc.qmd']) == fc.CLEAN
    assert ran == ['doc.qmd']


def test_main_says_how_to_get_pyyaml_when_it_is_missing(tmp_path, monkeypatch, capsys):
    (tmp_path / 'doc.py').write_text(PY, encoding='utf-8')
    # None in sys.modules makes `import yaml` fail as if it were absent.
    monkeypatch.setitem(sys.modules, 'yaml', None)
    with pytest.raises(SystemExit) as died:
        fc.main([str(tmp_path)])
    assert died.value.code == fc.CRASHED
    assert capsys.readouterr().err == fc.NEEDS_YAML + '\n'
    assert 'pip install "figtools[check]"' in fc.NEEDS_YAML
    # The functions that read YAML say the same when called directly.
    with pytest.raises(ImportError, match=r'figtools\[check\]'):
        fc.front_matter('---\nfig-width: 5\n---', 'doc.py')


def test_importing_check_needs_no_pyyaml_and_keeps_the_backend():
    """Importing the module from a notebook changes nothing: the backend is
    set when the check runs, and PyYAML is imported where it is used."""
    code = ('import sys; sys.modules["yaml"] = None\n'
            'import matplotlib; matplotlib.use("pdf")\n'
            'import figtools.check\n'
            'print(matplotlib.get_backend())\n')
    done = subprocess.run([sys.executable, '-c', code], capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == 'pdf'


def test_check_reads_a_notebooks_front_matter_from_its_raw_cell(tmp_path):
    nb = {'cells': [
        {'cell_type': 'raw', 'source': ['---\n', 'fig-width: 4\n', '---\n']},
        {'cell_type': 'code', 'source': [
            'import matplotlib.pyplot as plt\n',
            'fig, ax = plt.subplots()\n',
            'ax.plot([0, 1], [0, 1])\n',
        ]},
    ], 'nbformat': 4}
    doc = tmp_path / 'doc.ipynb'
    doc.write_text(json.dumps(nb), encoding='utf-8')
    n, found = fc.check(doc)
    assert (n, found) == (1, [])
    assert tuple(plt.rcParams['figure.figsize']) == (4.0, 5.0)
