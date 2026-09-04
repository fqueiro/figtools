"""fig-check runs documents of every format and measures their figures."""

import json

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

import plotsmith.check as fc

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
import plotsmith as ps
ps.setup()

# %%
if __name__ == '__main__':
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label='a')
    ps.label_lines(ax)
'''

QMD = '''---
fig-width: 5
---

```{python}
import matplotlib.pyplot as plt
import plotsmith as ps
ps.setup()
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label='a')
ps.label_lines(ax)
```
'''


def test_check_runs_a_qmd_and_honours_its_figure_size(tmp_path):
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD, encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []


def test_check_reports_a_failed_inline_assert_instead_of_dying(tmp_path):
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD.replace('ps.label_lines(ax)',
                               'ps.label_lines(ax)\nassert False'),
                   encoding='utf-8')
    n, found = fc.check(doc)
    assert any('assert' in line for line in found)


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
    assert tuple(plt.rcParams['figure.figsize']) == (5.0, 4.8)


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
            'import plotsmith as ps\n',
            'ps.setup()\n',
            'fig, ax = plt.subplots()\n',
            "ax.plot([0, 1], [0, 1], label='a')\n",
            'ps.label_lines(ax)\n',
        ]},
    ], 'nbformat': 4}
    doc = tmp_path / 'doc.ipynb'
    doc.write_text(json.dumps(nb), encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []
