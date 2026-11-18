# svg-cleaner

Clean up SVG files from the command line or from Python.

`svg-cleaner` was built after [scour](https://github.com/scour-project/scour),
[svgo](https://github.com/svg/svgo) and
[oxvg](https://github.com/noahbald/oxvg), and adds what those tools do not:
it removes the invisible geometry that plotting libraries keep inside
rectangular clip paths, and it repairs mermaid-cli exports so Inkscape lays
them out like a browser does.

```
$ svg-cleaner plot.svg
plot.svg -> plot.clean.svg  192.6 KiB -> 74.9 KiB (-61.1%)
```

Targets Windows, macOS and Linux; Python 3.10+. Not published on PyPI yet —
install from a checkout (see below).

## What it does

* strips comments, `<metadata>` and editor-specific elements/attributes
* inlines `<style>` rules with the real CSS cascade and deletes the block
* shortens ids, removes unused definitions and rewrites every reference
* rounds coordinates and path data to a chosen precision
* trims stroked paths against rectangular `clipPath`s (Bokeh exports)
* repairs mermaid-cli exports for Inkscape (foreignObject, labels, subgraphs)
* writes atomically, so an interrupted run never leaves a half-written SVG

## Install

```bash
uv tool install --from . svg-cleaner     # from a checkout
uv tool install --editable .             # editable, for hacking on it
uvx --from . svg-cleaner plot.svg        # run without installing
```

Or add it to a project with `uv add --editable .` and import it (see the
[Python API](#python-api)).

## Command line

```bash
svg-cleaner plot.svg                        # -> plot.clean.svg
svg-cleaner plot.svg -o out/plot.svg        # explicit destination
svg-cleaner plot.svg --inplace              # overwrite the input
svg-cleaner -r charts/ -o cleaned/          # every *.svg below charts/
cat in.svg | svg-cleaner - > out.svg        # stdin -> stdout
svg-cleaner diagram.svg --profile mermaid   # repair a mermaid-cli export
svg-cleaner plot.svg --drop-clip --trim-dashed
svg-cleaner plot.svg --minify --precision 2
svg-cleaner --check assets/*.svg            # CI gate, writes nothing
svg-cleaner --list-passes                   # show the pipeline
```

The summary line and warnings go to **stderr** when the document is written to
stdout, so `-o -` stays pipeable.

### Input and output

| Option | Meaning |
| --- | --- |
| `INPUT...` | SVG files, directories (with `-r`), or `-` for stdin |
| `-o`, `--output PATH` | output file, or a directory when there are several inputs |
| `--inplace` | overwrite the input files |
| `-r`, `--recursive` | expand directories to their `*.svg` files |
| `--suffix SUFFIX` | suffix for generated names (default `.clean`) |
| `--check` | write nothing; exit 1 if a file would change |
| `--dry-run` | report what would happen without writing |
| `--strict` | treat warnings as errors |
| `-q`, `--quiet` | only print errors |
| `-v`, `--verbose` | print per-pass statistics |
| `--version` | print the version and exit |

Exit codes: `0` success, `1` a file failed or `--check` found pending changes,
`2` usage or configuration error.

### Cleanup options

| Option | Meaning |
| --- | --- |
| `--profile {auto,generic,bokeh,mermaid}` | which profile to apply (default: detect) |
| `--precision N` | decimal places for coordinates and path data (default 3) |
| `--path-mode {keep,absolute,relative}` | rewrite path commands (default keep) |
| `--pretty` / `--minify` | indented output (default) or compact |
| `--indent N` | indent width for pretty output (default 2) |
| `--config FILE` | TOML configuration (default: `svg-cleaner.toml` or `pyproject.toml`) |
| `--drop-clip` | remove a clip reference once its geometry is trimmed |
| `--clip-inset auto\|none\|PX` | inset used with `--drop-clip` (default: stroke/2) |

### Pass toggles

Every toggle is on unless noted; `--no-…` switches it off.

| Toggle | Effect |
| --- | --- |
| `--inline-css` | inline `<style>` rules, then delete the block |
| `--remove-metadata` | strip `<metadata>` (comments and editor namespaces are always removed) |
| `--cleanup-ids` | dedupe, shorten referenced ids, drop unused ones |
| `--collapse-groups` | replace single-child `<g>` wrappers |
| `--remove-hidden` | remove invisible elements |
| `--trim-clip` | trim unfilled strokes against rectangular clips |
| `--trim-dashed` | also trim dashed strokes (**off**: shifts the dash phase) |
| `--fix-mermaid-labels` | re-place mermaid labels and subgraph titles (foreignObject conversion still runs) |

`structure`, `style-attributes`, `visibility` and `geometry` always run. Their
unconditional parts have no switch: comments, editor namespaces and empty
groups are always dropped, `style` properties are always moved to attributes,
unreferenced definitions are always pruned, and numbers are always rounded.
`--no-remove-metadata` only keeps `<metadata>`; `--no-remove-hidden` only keeps
hidden elements.

### Mermaid tuning

The defaults match a browser's layout; the knobs from the original repair
script are still available:

| Option | Default |
| --- | --- |
| `--baseline EM` | `0.35` — vertical shift of label rows |
| `--line-height EM` | `1.1` — row spacing |
| `--font-size PX` | read from the SVG |
| `--cluster-font-size PX` | same as `--font-size` |
| `--cluster-align left\|center\|right` | `center` |
| `--cluster-pad-top PX` / `--cluster-pad-x PX` | `6` / `8` |
| `--label-dx PX` / `--label-dy PX` | `0` |
| `--fo-font-family` / `--fo-fill` | inherit from the SVG |

## Profiles

`--profile auto` (the default) recognises:

* **mermaid** — mermaid ids, `node`/`cluster`/`edgeLabel` classes, label
  `foreignObject`s;
* **bokeh** — `data-bokeh-*` attributes, `bk-*` classes, and the transparent
  `paint-order="stroke"` background path Bokeh emits;
* **generic** — anything else.

The clip pass runs for every profile, because trimming a stroked path against
a rectangle is always safe; the mermaid label repair only runs for `mermaid`.

## Python API

```python
from pathlib import Path

from svg_cleaner import CleanConfig, clean, clean_file, describe_passes
from svg_cleaner.errors import ParseError, StrictWarning, SvgCleanerError

# Clean a file and write the result (atomically):
result = clean_file("plot.svg", "plot-clean.svg", CleanConfig(profile="bokeh"))

print(result.stats["bytes_after"], result.changed)   # size, did bytes change?
print(result.svg[:80])         # the cleaned document itself
print(result.profile)          # resolved profile: "bokeh"
print(result.stats)            # {"bytes_before": …, "bytes_after": …, "passes": {…}}
print(result.stats["passes"])  # {"structure": 0, "clip": 3, …}

# Keep the result in memory instead of writing it:
text = Path("logo.svg").read_text(encoding="utf-8")
cleaned = clean(text, CleanConfig(precision=2, pretty=False))
Path("logo.clean.svg").write_text(cleaned.svg, encoding="utf-8")

# bytes work too, e.g. straight from a network response:
cleaned = clean(response.content, CleanConfig(profile="mermaid"))

# Turn a pass off, or override one knob:
config = CleanConfig(
    disabled_passes=("clip", "visibility"),
    trim_dashed=True,
    drop_clip=True,
    cluster_align="left",
)
cleaned = clean_file("chart.svg", config=config)   # no destination: nothing written

# Warnings are returned unless strict mode is on:
if cleaned.warnings:
    print("\n".join(cleaned.warnings))
try:
    clean("<html/>", CleanConfig(strict=True))
except StrictWarning as exc:
    print(exc.warnings)
except ParseError as exc:      # not well-formed or not an <svg>
    print("skipped:", exc)
except SvgCleanerError as exc:  # base class for everything above
    print("failed:", exc)

# The pass list is public:
print(describe_passes())       # [("structure", "..."), …]
```

Notes:

* `clean()` accepts `str` or `bytes` and never touches the filesystem.
* `clean_file(source, destination=None, config=None)` writes with an atomic
  rename when `destination` is given.
* `CleanConfig(strict=True)` raises `StrictWarning` instead of returning
  warnings; `ParseError` is raised for malformed or non-SVG input.
* Every class and exception named above is importable from `svg_cleaner` or
  `svg_cleaner.errors`.

## Passes

The pipeline runs in this order and every pass is idempotent:

| Pass | What it does |
| --- | --- |
| `structure` | comments, `<metadata>`, editor namespaces/attributes, empty groups |
| `collapse-groups` | drops `<g>` wrappers with one child and no attributes |
| `styles` | inlines `<style>` with the real cascade (specificity, `!important`, source order) and deletes it |
| `style-attributes` | moves presentation properties to attributes, drops initial values where inheritance allows |
| `mermaid` | `foreignObject` → `<text>`, node/edge label centring, subgraph title placement |
| `clip` | trims unfilled strokes against rectangular clip paths |
| `visibility` | removes hidden elements, unused gradients/clipPaths/masks/defs |
| `geometry` | rounds numbers and path data, drops identity transforms |
| `ids` | dedupes, shortens referenced ids, removes unused ones, rewrites `url(#…)`, `href`, `aria-*` and SMIL timing references |

### Clip trimming (Bokeh)

Bokeh keeps every off-plot data point in the file and relies on a `clipPath`
to hide it. The pass walks each stroked, unfilled `<path>`, finds the nearest
ancestor (or itself) with `clip-path`, resolves the clip to an axis-aligned
rectangle, and clips the path data:

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
  multi-shape clips, filled shapes, `pathLength`) is left byte-identical and
  reported as a warning.

Measured on a real Bokeh export (800×530, two lines and a legend):
192.6 KiB → 74.9 KiB (−61.1 %) by default and 63.2 KiB (−67.2 %) with
`--trim-dashed`; 10 duplicated `clipPath`s were reduced to the 4 that are
referenced, and every trimmed stroke was verified to lie inside its clip
rectangle.

### Mermaid repair

Node labels inside a cluster are no longer re-placed as subgraph titles (a bug
in the first script version), and `<style>` is inlined before anything is
measured — including rules scoped to the root id (`#my-svg .node rect`), which
mermaid emits for every diagram. If no CSS rule can be matched at all, the
stylesheet is kept and a warning is reported rather than silently losing the
styling.

Measured on a 27-node mermaid-cli flowchart: 35.4 KiB → 32.1 KiB, and every
node, edge and subgraph label row lands at the same absolute position as the
reference repaired file.

## Configuration file

`svg-cleaner.toml`, `[tool.svg-cleaner]` in `pyproject.toml`, or a file passed
with `--config`. Command-line flags win, and unknown keys are rejected so typos
fail loudly.

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

From Python, use `load_config_file(Path("pyproject.toml"))` to get the same
`CleanConfig`.

## Limitations

* CSS shorthand/longhand expansion is not implemented; `var()` and at-rules are
  dropped with a warning while everything else is inlined.
* Arcs (`A`) are never trimmed, and filled shapes are never rewritten.
* Trimming changes where a dashed stroke starts (hence the opt-in flag).
* Ids referenced from outside the document (host-page CSS or JavaScript) cannot
  be detected; only the root id is always preserved.
* No rendering or pixel-diff check is built in; verify visually when a change
  matters.

## Inspiration and credits

This tool would not exist without these projects:

* [scour](https://github.com/scour-project/scour) — the Python SVG optimiser;
  its option set (metadata stripping, id shortening, viewBox handling,
  precision) shaped the pass list.
* [svgo](https://github.com/svg/svgo) — the plugin pipeline and its `cleanupIds`
  / `collapseGroups` / `inlineStyles` / `removeMetadata` behaviour, which the
  pass names and ordering follow.
* [oxvg](https://github.com/noahbald/oxvg) — a fast SVG toolchain in Rust; its
  CLI shape (optimise, lint, stream stdin → stdout) and its plugin parity goals
  were the model for the command-line interface.
* [graphics-to-clipboard](https://github.com/fathi0amir/graphics-to-clipboard) —
  the author's own small tool; its uv packaging, `uv tool install` instructions
  and the "library first, thin CLI on top" split are mirrored here.

The Bokeh clip-trimming and mermaid repair passes are ports of scripts written
for this project, and the reference cleaned files produced by those scripts
were used to validate the ports.

## AI disclosure

The code, tests and documentation were produced with an AI coding assistant
(Freebuff / Codebuff) working from a reviewed plan and validated against real
Bokeh and mermaid exports. The test suite and the commit history are the record
of that work. `svg-cleaner` does not call any AI service at runtime and makes no
network requests; review the code and try it on a copy of your own files before
relying on it in a critical pipeline.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
uv build
```

CI runs the test suite on Ubuntu, macOS and Windows with Python 3.10 and 3.13.
