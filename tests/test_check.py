"""fig-check runs documents of either format and measures their figures."""

import json

import matplotlib

matplotlib.use('Agg')

import plotsmith.check as fc

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
