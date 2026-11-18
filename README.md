# svg-cleaner

Clean up SVG files from the command line or from Python.

`svg-cleaner` was built after [scour](https://github.com/scour-project/scour),
[svgo](https://github.com/svg/svgo) and [oxvg](https://github.com/noahbald/oxvg),
and adds what those tools do not: it removes the invisible geometry that
plotting libraries keep inside rectangular clip paths, and it repairs
mermaid-cli exports so Inkscape lays them out like a browser does.

Works on Windows, macOS and Linux; Python 3.10+.

```
$ svg-cleaner plot.svg
plot.svg -> plot.clean.svg  192.6 KiB -> 63.2 KiB (-67.2%)
```

## Install

```bash
uv tool install --from . svg-cleaner     # from a checkout
uv tool install --editable .             # editable, for hacking on it
uvx --from . svg-cleaner plot.svg        # run without installing
```

Or add it to a project: `uv add --editable .`, then use the library API.

## Command line

```
svg-cleaner INPUT... [-o PATH] [--inplace] [-r] [--suffix .clean]
            [--profile auto|generic|bokeh|mermaid] [--precision N]
            [--check] [--dry-run] [--strict] [-q|-v]
```

| Option | Meaning |
| --- | --- |
| `-o`, `--output` | output file, or directory for several inputs |
| `--inplace` | overwrite the input files |
| `-r`, `--recursive` | expand directories to their `*.svg` files |
| `--suffix` | suffix for generated names (default `.clean`) |
| `--check` | write nothing, exit 1 if a file would change (CI gate) |
| `--dry-run` | report what would happen, write nothing |
| `--strict` | turn warnings into an error |
| `--precision` | decimal places for coordinates and path data (default 3) |
| `--path-mode` | `keep` (default), `absolute` or `relative` |
| `--pretty` / `--minify` | indented or compact output |
| `-` | read from stdin, or write to stdout when used with `-o -` |

Every pass can be switched off with its `--no-…` form, e.g.
`--no-inline-css`, `--no-cleanup-ids`, `--no-remove-hidden`,
`--no-collapse-groups`, `--no-trim-clip`, `--no-fix-mermaid-labels`.
`--list-passes` prints the pipeline.

Exit codes: `0` success, `1` a file failed or `--check` found pending changes,
`2` usage or configuration error.

### Profiles

`--profile auto` (the default) recognises mermaid-cli exports (mermaid ids,
`node`/`cluster` classes, label `foreignObject`s), Bokeh exports
(`data-bokeh-*`, `bk-*` classes, the transparent `paint-order="stroke"`
background) and falls back to `generic`. The clip pass runs for every profile,
because trimming a stroked path against a rectangle is always safe; the
mermaid label repair only runs for `mermaid`.

## Library

```python
from svg_cleaner import CleanConfig, clean, clean_file

result = clean_file("plot.svg", "plot-clean.svg", CleanConfig(profile="bokeh"))
result.svg        # cleaned document
result.changed    # did the bytes change?
result.warnings   # e.g. "clip: <path>: dashed clipped strokes, left as is"
result.stats      # {"bytes_before": …, "bytes_after": …, "passes": {…}}
```

`clean()` accepts `str` or `bytes`; parse failures raise `ParseError`, and
`CleanConfig(strict=True)` raises `StrictWarning` instead of returning warnings.
Errors derive from `SvgCleanerError`.

## Passes

The pipeline runs in this order and every pass is idempotent:

| Pass | What it does |
| --- | --- |
| `structure` | comments, `<metadata>`, editor namespaces/attributes, empty groups |
| `collapse-groups` | drops `<g>` wrappers with one child and no attributes |
| `styles` | inlines `<style>` with the real cascade (specificity, `!important`, source order) and deletes it |
| `style-attributes` | moves presentation properties to attributes, drops initial values |
| `mermaid` | `foreignObject` → `<text>`, node/edge label centring, subgraph title placement |
| `clip` | trims unfilled strokes against rectangular clip paths |
| `visibility` | removes hidden elements, unused gradients/clipPaths/masks/defs |
| `geometry` | rounds numbers and path data, drops identity transforms |
| `ids` | dedupes, shortens referenced ids, removes unused ones, rewrites every `url(#…)`/`href`/`aria-*` reference |

### Clip trimming (Bokeh)

Bokeh keeps every off-plot data point in the file and relies on a
`clipPath` to hide it. The pass walks each stroked, unfilled `<path>`, finds
the nearest ancestor (or itself) with `clip-path`, resolves the clip to an
axis-aligned rectangle, and clips the path data:

* clip shapes: a `<rect>` or an axis-aligned four-corner `<path>` (Bokeh emits
  `M … L … L … L … L … Z`), in `userSpaceOnUse` units;
* `translate`/`scale`/`rotate`/`matrix` transforms are composed and inverted,
  so transformed glyphs work;
* straight lines are cut with Liang–Barsky, curves with recursive Bézier
  subdivision (quadratics are elevated to cubics);
* the `clip-path` attribute is kept by default, so stroke caps are still
  clipped exactly as before;
* `--drop-clip` removes the now-redundant reference, and only then
  `--clip-inset` (default: half the stroke width) keeps round/butt caps inside
  the old boundary;
* dashed strokes are skipped by default because trimming restarts the dash
  phase; `--trim-dashed` opts in;
* anything unsupported (arcs, markers, `objectBoundingBox`, rounded or
  multi-shape clips, filled shapes) is left byte-identical and reported as a
  warning.

Measured on a real Bokeh export (800×530, two lines and a legend):
192.6 KiB → 63.2 KiB (−67.2 %), with 10 duplicated `clipPath`s reduced to the 4
that are referenced, and every trimmed stroke verified to lie inside its clip
rectangle.

### Mermaid tuning

For mermaid-cli diagrams the defaults match a browser's layout; the knobs from
the original repair script are still available:
`--baseline` (0.35 em), `--line-height` (1.1 em), `--font-size`,
`--cluster-font-size`, `--cluster-align left|center|right`,
`--cluster-pad-top`, `--cluster-pad-x`, `--label-dx`, `--label-dy`,
`--fo-font-family`, `--fo-fill`.

Node labels inside a cluster are no longer re-placed as subgraph titles (a bug
in the first script version), and `<style>` is inlined before anything is
measured.

## Configuration file

`svg-cleaner.toml`, `[tool.svg-cleaner]` in `pyproject.toml`, or `--config`
file. Command-line flags win.

```toml
[tool.svg-cleaner]
profile = "auto"
precision = 3
trim_clip = true
trim_dashed = false
drop_clip = false
inline_css = true
cleanup_ids = true
pretty = true
indent = 2
cluster_align = "center"
```

Unknown keys are rejected, so typos fail loudly.

## Limitations

* CSS shorthand/longhand expansion is not implemented; `var()` and at-rules are
  dropped with a warning while everything else is inlined.
* Arcs (`A`) are never trimmed, and filled shapes are never rewritten.
* Trimming changes where a dashed stroke starts (hence the opt-in flag).
* No rendering or pixel-diff check is built in; verify visually when a change
  matters.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv build
```

CI runs the test suite on Ubuntu, macOS and Windows with Python 3.10 and 3.13.
