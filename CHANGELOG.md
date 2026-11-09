# Changelog

Each release lists first the changes that alter how a figure is drawn, so
that a project can tell whether upgrading will change its figures, and then
the steps to upgrade.

## 0.10.0 (2026-11-09)

Versions up to 0.9 wrote each figure's plotted data inside its files: into
every PDF `save` wrote, in its document information, which pdfLaTeX and
LuaLaTeX copy into any document that includes the figure, so a paper
carried the data of every figure in it; and into every PNG a notebook
displayed, in a text chunk, so a self-contained HTML page carried the data
of every figure on it. This release keeps that data out of every figure
file, and so out of every paper and page. `save` writes it instead into a
plain-text companion beside the PDFs, with the line of code that called
`save`, and a displayed figure's digest goes into a store outside the
project. No figure is drawn differently, and code written for 0.9 runs
unchanged unless it calls the functions of `figtools.digest` that wrote or
read digests inside image files (*Migrating from 0.9* below).

### Changes that alter figures

- The PDFs `save` writes carry no digest. Their one figtools entry is the
  fingerprint, which moves from the document information to the document
  catalog, which no LaTeX engine copies into a document. The first run
  after upgrading writes every PDF once more.
- Figures a notebook kernel displays, as PNG, JPEG, SVG or PDF, are the
  images matplotlib draws, with nothing added.

### Other changes

- `save(fig, path)` writes `path.digest.txt` beside the PDFs: the figure's
  name; `source: <file>:<line>`, where `save` was called, the file relative
  to the companion's folder with forward slashes (absolute where no
  relative path exists) and, in a notebook kernel such as a Quarto render's,
  the document holding the calling cell, left out when the caller cannot be
  placed with certainty; a `file:` line per PDF with its size; and every
  plotted value, one per line, with no thinning. The text is
  deterministic, and `save` rewrites the file only when it changes other
  than in the line number of the call, so companions can be tracked in
  version control, where a change to a figure shows as a diff, while the
  PDFs are ignored. It is written on every call, also when the PDFs are
  current, and not under `fig-check`. With the digest off it holds the
  source and file lines and says the digest is off.
- A figure a notebook kernel displays has its digest filed in the store,
  `~/.local/share/render-digests/_figures/<sha256>.json` (or under
  `FIGTOOLS_DIGEST_DIR`), keyed by the hash of the image's bytes. The
  post-render hook finds each figure of a rendered page there by the hash
  of the image the page embeds or links, now for SVG and JPEG figures as
  well as PNG and PDF.
- A document rendered to PDF has its figures' digests recorded by the
  kernel in drawing order, since Quarto deletes the figure files after
  LaTeX has included them; the hook takes the newest render of the
  document's present source. It no longer reads digests out of the
  compiled PDF.
- `render-digest figure <file>` prints the companion of a PDF `save` wrote
  or of one of its variants, or the store's digest of an image a kernel
  displayed, and says when there is none.
- `render-digest prune [<project folder>]` lists the digests of documents
  and projects that no longer exist, store entries neither written nor
  read in 30 days and, given a project folder, companions none of whose
  PDFs exists, and removes them after asking (`--yes` skips the question).
- `fig-check --figs` writes plain PNGs and files their digests in the
  store.
- A digest whose recorded project root is a junction to the current
  project, such as a folder's former name that Dropbox leaves behind when
  it moves the folder, is recognised as the project's own, and the next
  render replaces it; before, the render refused it as another project's.

### Migrating from 0.9

- Figures and documents made with 0.9 or earlier still carry their data.
  Rerunning the code that saves the figures rewrites the PDFs without it;
  rebuild the papers that include them and render the pages again.
- Track `*.digest.txt` beside the figures and ignore the PDFs, if the
  repository tracked the PDFs to review figure changes.
- `figtools.digest` no longer has `KEY`, `encode`, `decode`, `png_text`,
  `from_png`, `pdf_metadata`, `pdf_digests`, `from_pdf` or `from_image`,
  which wrote and read digests inside image files. Read a saved figure's
  digest from its companion (`find_companion`, `read_companion`) and a
  displayed figure's from the store (`recall`).

## 0.9.0 (2026-10-19)

`save` leaves a PDF that is already current as it is, so rerunning a
document writes only the figures that changed. Code written for 0.8 runs
unchanged; `save` still returns the paths of its PDFs, those it found
current among them.

### Changes that alter figures

- The PDFs `save` writes gain one entry in their document information, the
  fingerprint of what they were drawn from, so the first run after
  upgrading writes every PDF once more.
- A figure whose files are all current is not redrawn at the end of `save`,
  so its on-screen rendering can differ from one drawn after writing by
  antialiasing noise at the edges of lines, sub-pixel and invisible;
  positions, values and labels are the same.

### Other changes

- `save` leaves a PDF as it is when the figure, the rcParams, the fonts,
  the versions, the target, its width and the digest choice are those it
  was written from, which the fingerprint records; rerunning a document
  then writes only the figures that changed (17 unchanged figures: about
  16 s of saving down to under 1 s). A figure that cannot be pickled is
  written every time, as before.
- The README's passage on coding agents says what looking at a figure costs
  an agent (time, tokens, missed overlaps, approximate values, verdicts
  that vary) rather than that an agent cannot look at all.

## 0.8.2 (2026-10-15)

Documentation only: the README says how figtools serves coding agents, and
credits where its design came from. Nothing in the package changes.

## 0.8.1 (2026-10-15)

No change alters a figure, and code written for 0.8.0 runs unchanged.

- `key` accepts entries that share a colour, as a key naming line styles
  has ('WCDE, solid'; 'census rounds, dashed'). 0.8.0 refused them, which
  broke keys whose words already tell their entries apart. The check meant
  for the colour cycle's eighth series stays in `label_lines`, where the
  cycle applies.
- Under `fig-check`, `save` measures the figure at each size and raises as
  before, but writes nothing, so checking a document leaves its figure
  files as they were.
- The README opens with what figtools does, the digest among it, and
  leaves the style choices for last.

## 0.8.0 (2026-10-15)

The first release on PyPI. It fixes the defects a review for public release
found and makes the interface consistent, which breaks some code written
for 0.7.0; *Migrating from 0.7.0* below lists what to change.

### Changes that alter figures

- `save` writes each PDF at the exact width of its target, 5.9 in for the
  paper and 4.1 in for slides by default. 0.7.0 cropped each PDF to its
  drawn content and padded it, so a figure meant to be 5.9 in wide could
  come out 6.02 in, and two figures built alike came out at different
  widths. PDFs included in LaTeX at natural size change size on the page.
- The colour cycle runs through all seven colours of `PALETTE`. In 0.7.0 it
  had four, so a fifth series took the first series' colour.
- `binscatter` keeps observations with the same x in one bin, cutting the
  bins at quantiles of x as Stata's `xtile` does. Where x has mass points
  there are fewer bins than `bins`, and where x takes at most `bins` values
  each value is one bin. 0.7.0 cut equal-count slices, which split tied
  values across bins and made the bin means depend on the order of the
  rows.
- `binscatter` draws its fit line from the first bin mean to the last, as
  Stata's `binscatter` does. In 0.7.0 it ran from the smallest observation
  to the largest, so with skewed x it ran far past the dots and set the
  width of the panel.
- `label_lines` labels each line at the end that is rightmost on screen. In
  0.7.0 a series stored newest first was labelled at its left end, and the
  panel was cut off there.
- `label_lines` on a log x-axis: the gap between a line's end and its
  label, and the pad at the end of the panel, are shares of the panel's
  width on screen. In 0.7.0 they were in data units, and a label touched
  its line's end.
- `label_points` moves its labels clear of plotted lines and strokes
  (dumbbell connectors, dot-plot stems, intervals) as well as of other
  labels and points.
- The N and R² note of `label_scatter(note=True)` is printed at the running
  text size, half a point below the base; in 0.7.0 it was one point
  smaller.
- A colour given as an RGB tuple to `dotplot`, `label_points` or
  `label_scatter` is one colour for every row. 0.7.0 read the tuple as
  three colours, one per row: sorting could permute them, so a red plot
  came out blue, and a plot with another number of rows raised an error.

### Migrating from 0.7.0

- Install from PyPI: `pip install figtools`. A uv project that depends on
  the git repository replaces the dependency with `uv remove figtools` and
  then `uv add figtools`.
- `guide` is renamed `refcurve`, so that it no longer shares a name with the
  usage guide `python -m figtools` prints. A digest's markdown calls its
  series a reference curve; the JSON keeps the role `guide`.
- `PALETTE` holds the seven colours, and `PALETTE_LONG` is removed: use
  `PALETTE`.
- Every drawing helper returns what it drew in a list, which a notebook
  displays as nothing, so a helper call ending a captioned cell no longer
  splits the figure from its caption:
  - `dotplot` returns the interval or stem collection followed by the
    markers. It returned the axes in 0.7.0; use the `ax` you passed it.
  - `pointrange` returns a list whose first element is the marker series
    it returned in 0.7.0, followed by the interval collection.
  - `band` and `refcurve` return their artist in a one-element list.
  - `label_lines`, `label_last`, `refline`, `shade` and `label_scatter`
    return their labels and artists; in 0.7.0 they returned None.
  - `key`, `label_points` and `save` return the same lists as in 0.7.0,
    in the list type a notebook does not display.
  - `binscatter` returns a named tuple `(x, y, slope)`, which unpacks as
    the plain tuple did.
- `save` writes exact widths (see above). Check figures included in a paper
  or slides at natural size after upgrading.
- `save` writes every target that `widths` names. With `targets` left out
  it writes paper, slides and then each other target in `widths`, so
  `save(fig, 'fig', widths={'poster': 9.0})` also writes `fig_poster.pdf`;
  0.7.0 wrote the poster only when `targets` named it. With `targets`
  given, a target that `widths` names and `targets` leaves out raises
  `ValueError` before anything is written; 0.7.0 ignored its width.
- PyYAML is no longer installed with figtools. `fig-check` needs the
  `check` extra: `pip install "figtools[check]"`.
- `fig-check` exits 2 when a document raises an error or the check cannot
  start, and 1 only when a document has overlaps. It checks every document
  even after one fails, and names crashed documents apart from those with
  overlaps. In 0.7.0 a crash exited 1, like an overlap; a script that tests
  for exit code 1 should test for any non-zero code.
- `label_lines` raises when two series it would name cannot be told apart
  by colour, line style and marker, as happens to the eighth series of an
  unstyled plot. Give such lines their own colours, dashes or markers.
  `key` raises when two entries share a colour.
- `label_lines` raises when called a second time on the same axes. Call it
  once, after plotting every series.
- `binscatter` keeps ties together (see above), and raises when `bins` is
  not a whole number.
- Arguments are checked. An unknown `key(loc=...)`, `refline(axis=...)` or
  `grid_axis` axis, a `dotplot` with fewer or more labels than values, and
  a `save` path ending in the extension of any format other than PDF that
  matplotlib saves to (`.png`, `.svg`, `.pgf` and the rest) each raise
  `ValueError`. 0.7.0 read some of these loosely: `key(loc='top left')`
  went to the lower left, and `save(fig, 'fig.png')` wrote `fig.png.pdf`.
- `save(..., retouch=True)` pickles `fig.v2` as `fig.v2.pkl`; 0.7.0 wrote
  `fig.pkl`. `load` reopens only a pickle that the same figtools version
  wrote and refuses any other, those of 0.7.0 included, since the private
  state a pickle holds changes between versions: rerun the code that drew
  the figure and save it again with `retouch=True`.
- `collisions` reports a label printed over a `label_scatter` name, which
  0.7.0 ignored, so a figure that passed may now fail `save` or
  `fig-check`.
- The Quarto post-render hook reads `QUARTO_PROJECT_DIR`,
  `QUARTO_PROJECT_OUTPUT_DIR` and `QUARTO_PROJECT_OUTPUT_FILES` from its
  environment and fails without them. A script that runs
  `render-digest post-render` outside Quarto has to set them.
- In a digest, an unlabelled series is named only by a `key` entry, a line
  of a key packed from offset boxes, or a legend entry in its colour. In
  0.7.0 any coloured note in axes coordinates named it, so a black panel
  letter could name a black series.

### New

- `setup(digest=False)` turns the digest off for every figure, and
  `save(..., digest=False)` for one figure's files. The digest stays on by
  default. A `digest` other than True or False (or None for `save`) raises
  `TypeError`.
- `style()`, a context manager that applies the style inside a `with` block
  and undoes it on leaving: the rcParams, the linear scale and the
  notebook's figure formatters.
- `__version__`.
- `label_lines` works on an inverted x-axis; 0.7.0 raised an error.
- `fig-check` accepts a document's file name as well as its stem, which
  tells apart two documents that share a stem.
- `figtools.digest`: `uninstall()` and `installed()`; `cache_root()` and
  the environment variable `FIGTOOLS_DIGEST_DIR`, which moves the digest
  cache; `source_for(output, output_dir, project)` and `SOURCES`.
- The Quarto post-render hook reads each figure linked beside the page, in
  the `<document>_files` folder Quarto writes by default, as well as those
  embedded in it, so a document no longer needs `embed-resources: true`
  for its digest.
- The hook skips a page made from plain Markdown (`.md`, `.markdown`,
  `.Rmd`), which runs no figtools code, and a PDF that no single document
  made, such as a book's, and prints a line saying why.
- A gallery of nine figures in `docs/gallery`, each with the script that
  draws it; the tests run every script.
- Python 3.14 is supported. The tests run on Linux, Windows and macOS, and
  each week against the newest release of every dependency.

### Fixed

- The Quarto post-render hook works in projects with an output folder
  (websites, books, `output-dir`): it finds each output's source through
  the paths Quarto passes. In 0.7.0 it failed the render.
- `setup()` in terminal IPython leaves the backend alone; it arms the
  digest only in a notebook kernel. In 0.7.0 it switched the backend to
  inline, and `plt.show()` opened no window.
- Two projects with the same folder name no longer overwrite each other's
  digests. The manifest records the project's path; while the other
  project's folder still holds its `_quarto.yml`, the hook refuses to
  overwrite its digest and `render-digest check` returns 2 for it. The
  digest of a project that has moved or been deleted is stale, and the
  next render replaces it.
- `render-digest where`, `check` and `extract` exit 2 with a one-line
  message for a document in no Quarto project. In 0.7.0 they printed a
  traceback and exited 1, which a script gating on `check` read as a
  changed source.
- `fig-check` reads the front matter of a percent-format `.py` held in a
  raw string, a markdown cell that opens with `r"""`. 0.7.0 missed that
  front matter and the figure size it set.
- `refline(..., color=...)`, `shade(..., color=...)`,
  `refcurve(..., zorder=...)` and `label_last(..., va=...)` override the
  defaults, and `label_last(..., xytext=..., textcoords=...)` replaces
  its offset. In 0.7.0 a keyword that matched a default raised
  `TypeError`.
- Importing `figtools.check` leaves matplotlib's backend alone; in 0.7.0
  the import switched it to Agg.

## Earlier versions

Versions up to 0.7.0 were installed from the git repository and have no
changelog; their changes are in the
[git history](https://github.com/fqueiro/figtools/commits/v0.7.0).
