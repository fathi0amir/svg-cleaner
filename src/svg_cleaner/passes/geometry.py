"""Geometry cleanup: precision, path serialization and identity transforms."""

from __future__ import annotations

import re

from lxml import etree

from ..document import collect, fmt, iter_elements
from ..pathdata import (
    PathDataError,
    format_commands,
    parse_commands,
    parse_transform,
    round_path_data,
    segments_to_commands,
    to_segments,
)
from .context import PassContext

NUMBER_ATTRIBUTES = {
    "x",
    "y",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x1",
    "y1",
    "x2",
    "y2",
    "fx",
    "fy",
    "offset",
    "stroke-width",
    "stroke-dashoffset",
    "stroke-miterlimit",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "stop-opacity",
    "flood-opacity",
    "stdDeviation",
    "startOffset",
    "refX",
    "refY",
    "markerWidth",
    "markerHeight",
    "textLength",
    "letter-spacing",
    "word-spacing",
    "font-size",
    "baseFrequency",
    "radius",
}

LIST_ATTRIBUTES = {"viewBox", "points", "stroke-dasharray", "values", "keyTimes"}
_NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


PROTECT_ZERO = {
    "width",
    "height",
    "r",
    "rx",
    "ry",
    "stroke-width",
    "font-size",
    "markerWidth",
    "markerHeight",
    "stdDeviation",
    "baseFrequency",
    "radius",
}


def _round_number(value: float, precision: int, protect_zero: bool = False) -> str | None:
    rounded = round(value, precision)
    if protect_zero and value != 0 and rounded == 0:
        # never collapse a real dimension to zero just because precision is low
        return None
    if rounded == 0:
        return "0"
    return fmt(rounded, precision)


def _round_attribute(value: str, precision: int, protect_zero: bool = False) -> str | None:
    try:
        number = float(value.strip())
    except ValueError:
        return None
    return _round_number(number, precision, protect_zero)


def _round_list(value: str, precision: int, protect_zero: bool = False) -> str:
    def replace(match: re.Match[str]) -> str:
        rounded = _round_number(float(match.group(0)), precision, protect_zero)
        return rounded if rounded is not None else match.group(0)

    return _NUMBER.sub(replace, value)


def _round_viewbox(value: str, precision: int) -> str:
    """Round a viewBox, never collapsing its width or height to zero."""
    tokens = _NUMBER.findall(value)
    if len(tokens) != 4:
        return value
    rounded: list[str] = []
    for index, token in enumerate(tokens):
        result = _round_number(float(token), precision, protect_zero=index >= 2)
        rounded.append(result if result is not None else token)
    return " ".join(rounded)


def _rewrite_path_data(data: str, ctx: PassContext) -> str | None:
    config = ctx.config
    try:
        if config.path_mode == "keep":
            return round_path_data(data, config.precision)
        segments = to_segments(parse_commands(data))
        commands = segments_to_commands(segments, mode=config.path_mode)
        return format_commands(commands, config.precision)
    except PathDataError as exc:
        ctx.warn("geometry", f"kept unparsable path data: {exc}")
        return None


def simplify_geometry(root: etree._Element, ctx: PassContext) -> int:
    """Round numbers, reserialize path data, drop identity transforms."""
    config = ctx.config
    precision = config.precision
    changed = 0

    for element in iter_elements(root):
        for name in NUMBER_ATTRIBUTES:
            value = element.get(name)
            if value is None:
                continue
            rounded = _round_attribute(value, precision, protect_zero=name in PROTECT_ZERO)
            if rounded is not None and rounded != value:
                element.set(name, rounded)
                changed += 1
        for name in LIST_ATTRIBUTES:
            value = element.get(name)
            if value is None:
                continue
            if name == "viewBox":
                rounded = _round_viewbox(value, precision)
            elif name == "stroke-dasharray":
                # a zero-length dash would change the pattern
                rounded = _round_list(value, precision, protect_zero=True)
            else:
                rounded = _round_list(value, precision)
            if rounded != value:
                element.set(name, rounded)
                changed += 1
        transform = element.get("transform")
        if transform is not None and parse_transform(transform).is_identity():
            del element.attrib["transform"]
            changed += 1

    for path in collect(root, ["path"]):
        data = path.get("d")
        if not data:
            continue
        rewritten = _rewrite_path_data(data, ctx)
        if rewritten is not None and rewritten != data:
            path.set("d", rewritten)
            changed += 1
    return changed


__all__ = ["simplify_geometry", "NUMBER_ATTRIBUTES"]
