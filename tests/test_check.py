"""fig-check runs documents of either format and measures their figures."""

import json

import matplotlib

matplotlib.use('Agg')

import fig_style.check as fc

QMD = '''---
fig-width: 5
---

```{python}
import matplotlib.pyplot as plt
import fig_style as ts
ts.setup()
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label='a')
ts.label_lines(ax)
```
'''


def test_check_runs_a_qmd_and_honours_its_figure_size(tmp_path):
    doc = tmp_path / 'doc.qmd'
    doc.write_text(QMD, encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []


def test_check_runs_an_ipynb_and_skips_magics(tmp_path):
    nb = {'cells': [
        {'cell_type': 'markdown', 'source': ['# not code\n']},
        {'cell_type': 'code', 'source': [
            '%config InlineBackend.figure_format = "retina"\n',
            'import matplotlib.pyplot as plt\n',
            'import fig_style as ts\n',
            'ts.setup()\n',
            'fig, ax = plt.subplots()\n',
            "ax.plot([0, 1], [0, 1], label='a')\n",
            'ts.label_lines(ax)\n',
        ]},
    ], 'nbformat': 4}
    doc = tmp_path / 'doc.ipynb'
    doc.write_text(json.dumps(nb), encoding='utf-8')
    n, found = fc.check(doc)
    assert n == 1
    assert found == []
