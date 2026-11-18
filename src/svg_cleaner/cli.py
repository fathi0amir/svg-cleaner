"""Command-line interface for svg-cleaner."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .api import CleanResult, clean
from .config import CleanConfig, find_config, load_config_file
from .document import atomic_write
from .errors import SvgCleanerError
from .passes import describe_passes

CONFIG_FLAGS = (
    "profile",
    "precision",
    "path_mode",
    "inline_css",
    "remove_metadata",
    "cleanup_ids",
    "collapse_groups",
    "remove_hidden",
    "trim_clip",
    "trim_dashed",
    "fix_mermaid_labels",
    "remove_comments",
    "remove_editor_data",
    "style_to_attributes",
    "xml_declaration",
    "drop_clip",
    "clip_inset",
    "strict",
    "pretty",
    "baseline",
    "line_height",
    "font_size",
    "cluster_font_size",
    "cluster_align",
    "cluster_pad_top",
    "cluster_pad_x",
    "label_dx",
    "label_dy",
    "fo_font_family",
    "fo_fill",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="svg-cleaner",
        description=(
            "Clean up SVG files: optimize structure, inline CSS, round coordinates, "
            "trim clipped strokes (Bokeh) and repair mermaid-cli exports for Inkscape."
        ),
        epilog=(
            "Examples:\n"
            "  svg-cleaner plot.svg                     # writes plot.clean.svg\n"
            "  svg-cleaner plot.svg -o plot-clean.svg\n"
            "  svg-cleaner diagram.svg --profile mermaid\n"
            "  svg-cleaner -r charts/ -o cleaned/       # batch\n"
            "  cat in.svg | svg-cleaner - > out.svg\n"
            "  svg-cleaner --check assets/*.svg         # CI gate"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "inputs", nargs="*", metavar="INPUT", help="SVG files, directories (with -r) or - for stdin"
    )
    parser.add_argument("-o", "--output", metavar="PATH", help="output file or directory")
    parser.add_argument("--inplace", action="store_true", help="overwrite the input files")
    parser.add_argument("-r", "--recursive", action="store_true", help="descend into directories")
    parser.add_argument(
        "--suffix", default=".clean", help="suffix for generated names (default: %(default)s)"
    )
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 if a file would change"
    )
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    parser.add_argument("--strict", action="store_true", default=None, help="warnings are errors")
    bool_action = argparse.BooleanOptionalAction
    parser.add_argument(
        "--remove-comments", action=bool_action, default=None,
        help="strip XML comments (default: on)",
    )
    parser.add_argument(
        "--remove-editor-data", action=bool_action, default=None,
        help="strip editor namespaces and attributes (default: on)",
    )
    parser.add_argument(
        "--style-to-attributes", action=bool_action, default=None,
        help="move style properties to attributes (default: on)",
    )
    parser.add_argument(
        "--xml-declaration", action=bool_action, default=None,
        help="write the <?xml …?> declaration (default: on)",
    )
    parser.add_argument(
        "--profile",
        choices=["auto", "generic", "bokeh", "mermaid"],
        default=None,
        help="which cleanup profile to apply (default: detect)",
    )
    parser.add_argument("--precision", type=int, default=None, metavar="N",
                        help="decimal places for coordinates (default: 3)")
    parser.add_argument("--path-mode", choices=["keep", "absolute", "relative"], default=None,
                        help="rewrite path commands as absolute or relative")
    parser.add_argument("--indent", type=int, default=None, metavar="N",
                        help="indent width for pretty output (default: 2)")
    parser.add_argument("--pretty", dest="pretty", action="store_true", default=None,
                        help="pretty-print the output (default)")
    parser.add_argument("--minify", dest="minify", action="store_true", default=None,
                        help="write compact output without indentation")
    parser.add_argument("--drop-clip", action="store_true", default=None,
                        help="remove clipPath references once the geometry is trimmed")
    parser.add_argument("--clip-inset", default=None, metavar="auto|none|PX",
                        help="inset applied when dropping clips (default: auto = stroke/2)")

    toggles = parser.add_argument_group("passes")
    for flag, help_text in (
        ("inline-css", "inline <style> rules (default: on)"),
        ("remove-metadata", "strip metadata and editor namespaces (default: on)"),
        ("cleanup-ids", "dedupe, shorten and drop ids (default: on)"),
        ("collapse-groups", "collapse redundant <g> wrappers (default: on)"),
        ("remove-hidden", "remove invisible elements (default: on)"),
        ("trim-clip", "trim unfilled strokes against rectangles (default: on)"),
        ("trim-dashed", "also trim dashed strokes; shifts the dash phase (default: off)"),
        ("fix-mermaid-labels", "re-place mermaid labels and titles (default: on)"),
    ):
        toggles.add_argument(
            f"--{flag}",
            dest=flag.replace("-", "_"),
            action=argparse.BooleanOptionalAction,
            default=None,
            help=help_text,
        )

    mermaid = parser.add_argument_group("mermaid tuning")
    mermaid.add_argument("--baseline", type=float, default=None, metavar="EM",
                         help="vertical shift of label rows (default: 0.35)")
    mermaid.add_argument("--line-height", type=float, default=None, metavar="EM",
                         help="row spacing (default: 1.1)")
    mermaid.add_argument("--font-size", type=float, default=None, metavar="PX",
                         help="label font size (default: read from the SVG)")
    mermaid.add_argument("--cluster-font-size", type=float, default=None, metavar="PX",
                         help="subgraph title size (default: same as --font-size)")
    mermaid.add_argument("--cluster-align", choices=["left", "center", "right"], default=None,
                         help="subgraph title alignment (default: center)")
    mermaid.add_argument("--cluster-pad-top", type=float, default=None, metavar="PX",
                         help="gap between subgraph top edge and title (default: 6)")
    mermaid.add_argument("--cluster-pad-x", type=float, default=None, metavar="PX",
                         help="gap to the side edge for left/right titles (default: 8)")
    mermaid.add_argument("--label-dx", type=float, default=None, metavar="PX",
                         help="horizontal nudge for node/edge labels")
    mermaid.add_argument("--label-dy", type=float, default=None, metavar="PX",
                         help="vertical nudge for node/edge labels")
    mermaid.add_argument("--fo-font-family", default=None,
                         help="font for converted foreignObject text")
    mermaid.add_argument("--fo-fill", default=None,
                         help="colour for converted foreignObject text")

    parser.add_argument("--config", metavar="FILE",
                        help="TOML configuration (default: svg-cleaner.toml or pyproject.toml)")
    parser.add_argument("--list-passes", action="store_true", help="list passes and exit")
    parser.add_argument("--only", metavar="PASSES", default=None,
                        help="run only these passes (comma-separated; see --list-passes)")
    parser.add_argument("--skip", metavar="PASSES", default=None,
                        help="skip these passes (comma-separated; see --list-passes)")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="show pass statistics")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _parse_inset(value: object) -> float | None:
    text = str(value).strip().lower()
    if text == "auto":
        return None
    if text == "none":
        return 0.0
    try:
        return float(text)
    except ValueError as exc:
        raise SvgCleanerError("--clip-inset expects auto, none or a number") from exc


def _build_config(args: argparse.Namespace) -> CleanConfig:
    if args.config:
        base = load_config_file(Path(args.config))
    else:
        base = find_config() or CleanConfig()
    overrides: dict[str, object] = {}
    for name in CONFIG_FLAGS:
        value = getattr(args, name, None)
        if value is not None:
            overrides[name] = value
    if "clip_inset" in overrides:
        overrides["clip_inset"] = _parse_inset(overrides["clip_inset"])
    if args.indent is not None:
        overrides["indent"] = args.indent
    if args.minify:
        overrides["pretty"] = False
    elif args.pretty:
        overrides["pretty"] = True
    return base.with_overrides(overrides)


def _expand(items: Sequence[str], recursive: bool) -> list[str]:
    paths: list[str] = []
    for item in items:
        if item == "-":
            paths.append(item)
            continue
        path = Path(item)
        if path.is_dir():
            if not recursive:
                raise SvgCleanerError(f"{item} is a directory (pass -r to recurse)")
            found = sorted(str(found_path) for found_path in path.rglob("*.svg"))
            if not found:
                raise SvgCleanerError(f"no SVG files under {item}")
            paths.extend(found)
        elif path.is_file():
            paths.append(item)
        else:
            raise SvgCleanerError(f"{item}: no such file")
    return paths


def _output_name(path: Path, suffix: str) -> str:
    separator = "" if suffix.startswith(".") else "."
    return f"{path.stem}{separator}{suffix}{path.suffix}"


def _plan(sources: Sequence[str], args: argparse.Namespace) -> list[str]:
    multiple = len(sources) > 1
    destinations: list[str] = []
    for source in sources:
        if source == "-":
            destinations.append("-" if args.output in (None, "-") else args.output)
            continue
        path = Path(source)
        if args.inplace:
            destinations.append(str(path))
            continue
        if args.output:
            target = Path(args.output)
            if target.is_dir() or multiple:
                destinations.append(str(target / _output_name(path, args.suffix)))
            else:
                destinations.append(str(target))
            continue
        destinations.append(str(path.with_name(_output_name(path, args.suffix))))
    return destinations


def _validate_destinations(destinations: Sequence[str]) -> None:
    normalized: dict[str, str] = {}
    for destination in destinations:
        if destination == "-":
            continue
        key = os.path.normcase(os.path.realpath(destination))
        if key in normalized:
            raise SvgCleanerError(
                f"multiple inputs would overwrite {destination}; use separate output paths"
            )
        normalized[key] = destination


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KiB"


def _label(source: str) -> str:
    return "<stdin>" if source == "-" else source


def _run_one(source: str, destination: str, config: CleanConfig, args: argparse.Namespace):
    if source == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(source).read_bytes()
    only = [part.strip() for part in args.only.split(",") if part.strip()] if args.only else None
    skip = [part.strip() for part in args.skip.split(",") if part.strip()] if args.skip else None
    result = clean(data, config, filename=_label(source), only=only, skip=skip)
    if args.check or args.dry_run:
        return result
    if destination == "-":
        sys.stdout.buffer.write(result.svg.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        atomic_write(Path(destination), result.svg.encode("utf-8"))
    return result


def _report(source: str, destination: str, result: CleanResult, args: argparse.Namespace) -> None:
    if args.quiet:
        for warning in result.warnings:
            print(f"svg-cleaner: {_label(source)}: warning: {warning}", file=sys.stderr)
        return
    before = result.stats["bytes_before"]
    after = result.stats["bytes_after"]
    percent = (after - before) / before * 100 if before else 0.0
    target = "<stdout>" if destination == "-" else destination
    suffix = " (dry run)" if args.dry_run else ""
    # never write the summary into the SVG stream when -o - is used
    stream = sys.stderr if destination == "-" else sys.stdout
    print(
        f"{_label(source)} -> {target}  {_human(before)} -> {_human(after)} "
        f"({percent:+.1f}%){suffix}",
        file=stream,
    )
    if args.verbose:
        passes = ", ".join(f"{name}={count}" for name, count in result.stats["passes"].items())
        print(f"  profile={result.profile}; {passes or 'nothing changed'}", file=sys.stderr)
    for warning in result.warnings:
        print(f"svg-cleaner: {_label(source)}: warning: {warning}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_passes:
        width = max(len(name) for name, _ in describe_passes())
        for name, description in describe_passes():
            print(f"{name:<{width}}  {description}")
        return 0

    try:
        config = _build_config(args)
    except SvgCleanerError as exc:
        print(f"svg-cleaner: error: {exc}", file=sys.stderr)
        return 2

    known_passes = {name for name, _ in describe_passes()}
    for raw in (args.only, args.skip):
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip()
            if part and part not in known_passes:
                parser.error(f"unknown pass {part!r}; see --list-passes")

    raw_inputs = list(args.inputs)
    if not raw_inputs:
        if sys.stdin is None or sys.stdin.isatty():
            parser.error("no input files (pass - to read from stdin)")
        raw_inputs = ["-"]
    if "-" in raw_inputs and len(raw_inputs) > 1:
        parser.error("stdin (-) cannot be combined with files")
    if "-" in raw_inputs and args.inplace:
        parser.error("--inplace cannot be used with stdin")
    if len(raw_inputs) > 1 and args.output == "-":
        parser.error("cannot write several files to stdout")
    if not args.suffix:
        parser.error("--suffix must not be empty")

    try:
        sources = _expand(raw_inputs, args.recursive)
    except SvgCleanerError as exc:
        parser.error(str(exc))
    destinations = _plan(sources, args)
    try:
        _validate_destinations(destinations)
    except SvgCleanerError as exc:
        parser.error(str(exc))

    changed = 0
    failures = 0
    for source, destination in zip(sources, destinations, strict=False):
        try:
            result = _run_one(source, destination, config, args)
        except SvgCleanerError as exc:
            print(f"svg-cleaner: {_label(source)}: error: {exc}", file=sys.stderr)
            failures += 1
            continue
        except OSError as exc:
            print(f"svg-cleaner: {_label(source)}: error: {exc}", file=sys.stderr)
            failures += 1
            continue
        if result.changed:
            changed += 1
        _report(source, destination, result, args)

    if failures:
        return 1
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
