# Contributing to figtools

Bug reports and pull requests are welcome at
https://github.com/fqueiro/figtools. A bug report helps most with a short
script that draws the figure, and the output of
`python -c "import figtools, matplotlib; print(figtools.__version__, matplotlib.__version__)"`.

## Setting up

The project uses [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/fqueiro/figtools
cd figtools
uv sync
```

`uv sync` installs figtools in editable mode with the development tools:
pytest, IPython and the inline backend for the notebook side of the digest,
Polars and pandas for the tests that pass their columns, and PyYAML for
`fig-check`.

## Tests

```sh
uv run pytest
```

Every change in behaviour comes with a test. A bad argument raises
`ValueError`, or `TypeError` when it has the wrong type, with a message
that names the function and says what to pass instead.

CI runs the suite three ways: with the lock file on Python 3.14 on Linux,
Windows and macOS; on Python 3.11 with the oldest version of every
dependency that `pyproject.toml` allows (`uv run --resolution
lowest-direct --python 3.11 pytest`), so the lower bounds stay true; and
on Python 3.14 with the newest release of every dependency, ignoring the
lock (`uv run --upgrade --python 3.14 pytest`), so a matplotlib release
that breaks the library shows up before a project's lock reaches it. The
suite runs on every push and pull request, and every Monday, so a
dependency released since the last push is tested without one. Run the
second and third checks in a separate copy of the repository: they replace
the project's environment and rewrite `uv.lock`.

## Code

- `src/figtools/__init__.py` holds the style, the label placement, the
  helpers and `save`; `check.py` the `fig-check` command; `digest.py` the
  digest and the `render-digest` command.
- Strings in single quotes. Comments give the reason for the code. Docstrings
  describe what the code does now, with no history.
- Label placement is measured. A change to the placement must keep
  `collisions` honest, and a new helper that draws text either takes part
  in the placement or is exempted from the check with its reason stated.
- A helper never sets a font size of its own: it resolves sizes through
  `_text_size()`, so that every text follows `setup(base=...)`.
- A drawing helper returns what it drew in a `_Drawn` list, which a
  notebook displays as nothing.

## The gallery

`docs/gallery` holds one script per kind of figure, each with the PNG the
README shows, and `docs/readme_figure.py` draws the README's first figure.
Each script raises if `collisions` finds anything. `tests/test_gallery.py`
runs every script, each from a copy in a temporary folder so the tracked
images stay as they are, and checks that every script has its image and
every image its script.

The gallery must stay clean. After a change that alters how figures are
drawn, regenerate the images by running the scripts
(`uv run python docs/gallery/<name>.py`), look at each one, and commit them
with the change.

## The digest

The digest is read by programs, so its format is a contract, described in
the docstring of `src/figtools/digest.py`. A new kind of artist or helper
mark that `describe` learns to read gets a test that draws it and checks
the values and labels that come back, and `markdown` and `csv_rows` learn
to write it.

## The changelog

Projects pin a version and upgrade when they choose, and the changelog is
how they decide. Every change that alters a figure (its size, colours,
label placement, a default, or what `collisions` reports) gets an entry in
`CHANGELOG.md` under *Changes that alter figures*, and every change that
breaks existing code gets a step under *Migrating*. Add the entries under
the next version in the same pull request as the change.

## Releasing

1. Set the version: `uv version X.Y.Z`. It updates `pyproject.toml` and
   `uv.lock`, which records the project's version; CI runs with `--locked`
   and fails if the two disagree.
2. Give the changelog entry its version and date.
3. Commit, push, and wait for the tests on `main` to pass. Publishing runs
   the tests with the lock file on Linux only, not on the other platforms
   or with the oldest and newest dependencies.
4. Before the first release only: on the
   [Publishing page](https://pypi.org/manage/account/publishing/) of the
   pypi.org account, add a pending publisher for the project `figtools`
   with owner `fqueiro`, repository `figtools`, workflow `publish.yml` and
   environment `pypi`; renaming any of them later breaks the upload.
   Optionally, create the environment `pypi` under the repository's
   Settings, Environments on GitHub and give it a required reviewer, so
   that each upload waits for approval; the workflow otherwise creates the
   environment on its first run, with no reviewer.
5. Tag the commit and push the tag:

   ```sh
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The `publish` workflow (`.github/workflows/publish.yml`) does the rest. It
checks that the tag names the version in `pyproject.toml`, runs the tests
with the lock file on Python 3.14, builds the sdist and the wheel, and
uploads them to PyPI by trusted publishing, so no token is stored
anywhere. PyPI never accepts the same version twice, so a release with a
mistake is fixed by a new version.
