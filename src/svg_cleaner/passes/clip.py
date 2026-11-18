"""Trim stroked paths against rectangular clip paths (the Bokeh case)."""

from __future__ import annotations

import re

from lxml import etree

from ..document import collect, inherited, localname, parse_length
from ..pathdata import (
    Bounds,
    Matrix,
    PathDataError,
    bounds_contains,
    clip_segments,
    format_commands,
    parse_commands,
    parse_transform,
    segments_to_commands,
    to_segments,
    transform_segments,
)
from .context import PassContext, describe

CLIP_REFERENCE = re.compile(r"url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)")

GEOMETRY_TAGS = {
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "image",
    "use",
    "foreignObject",
}


def _clip_reference(element: etree._Element) -> str | None:
    value = element.get("clip-path")
    if not value:
        return None
    match = CLIP_REFERENCE.fullmatch(value.strip())
    return match.group(1) if match else None


def _clip_carrier(element: etree._Element) -> tuple[etree._Element | None, str | None]:
    """Nearest element (self or ancestor) carrying a ``clip-path`` reference."""
    node: etree._Element | None = element
    while node is not None:
        reference = _clip_reference(node)
        if reference:
            return node, reference
        node = node.getparent()
    return None, None


def _find_id(root: etree._Element, identifier: str) -> etree._Element | None:
    for element in collect(root, ["clipPath"]):
        if element.get("id") == identifier:
            return element
    return None


def _rect_bounds(rect: etree._Element) -> Bounds | None:
    if rect.get("rx") or rect.get("ry"):
        return None  # rounded corners are not a rectangle for clipping purposes
    x = parse_length(rect.get("x"), 0.0)
    y = parse_length(rect.get("y"), 0.0)
    width = parse_length(rect.get("width"))
    height = parse_length(rect.get("height"))
    if x is None or y is None or width is None or height is None:
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, x + width, y + height)


def _path_bounds(path: etree._Element) -> Bounds | None:
    try:
        segments = to_segments(parse_commands(path.get("d")))
    except PathDataError:
        return None
    points: list[tuple[float, float]] = []
    for segment in segments:
        kind = segment[0]
        if kind == "M":
            points.append(segment[1])
        elif kind == "L":
            p0, p1 = segment[1], segment[2]
            if abs(p0[0] - p1[0]) > 1e-6 and abs(p0[1] - p1[1]) > 1e-6:
                return None  # not axis aligned
            points.extend((p0, p1))
        elif kind == "Z":
            p0, p1 = segment[1], segment[2]
            if abs(p0[0] - p1[0]) > 1e-6 and abs(p0[1] - p1[1]) > 1e-6:
                return None
            points.extend((p0, p1))
        else:
            return None
    if len(points) < 4:
        return None
    xs = sorted({round(point[0], 6) for point in points})
    ys = sorted({round(point[1], 6) for point in points})
    if len(xs) != 2 or len(ys) != 2:
        return None
    return (xs[0], ys[0], xs[1], ys[1])


def _clip_geometry(clip: etree._Element) -> tuple[Bounds, Matrix] | None:
    """Bounds and transform of a user-space rectangular clipPath."""
    if clip.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse":
        return None
    shapes = [
        child
        for child in clip
        if isinstance(child.tag, str) and localname(child) not in ("title", "desc")
    ]
    if len(shapes) != 1:
        return None
    shape = shapes[0]
    matrix = parse_transform(clip.get("transform")) @ parse_transform(shape.get("transform"))
    name = localname(shape)
    if name == "rect":
        bounds = _rect_bounds(shape)
    elif name == "path":
        bounds = _path_bounds(shape)
    else:
        bounds = None
    if bounds is None:
        return None
    return bounds, matrix


def _matrix_to(element: etree._Element, carrier: etree._Element) -> Matrix | None:
    """Matrix mapping the element's user space into the carrier's user space."""
    matrix = Matrix()
    node: etree._Element | None = element
    while node is not None and node is not carrier:
        matrix = parse_transform(node.get("transform")) @ matrix
        node = node.getparent()
    return matrix if node is carrier else None


def _points_of(segment: tuple) -> list[tuple[float, float]]:
    kind = segment[0]
    if kind == "M":
        return [segment[1]]
    if kind == "L":
        return [segment[1], segment[2]]
    if kind == "C":
        return [segment[1], segment[2], segment[3], segment[4]]
    if kind == "Q":
        return [segment[1], segment[2], segment[3]]
    if kind == "Z":
        return [segment[1], segment[2]]
    return [segment[1], segment[-1]]


def _all_points_inside(segments: list, bounds: Bounds) -> bool:
    return all(
        bounds_contains(bounds, point)
        for segment in segments
        for point in _points_of(segment)
    )


def _is_zero(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return float(value.strip().rstrip("%")) == 0
    except ValueError:
        return False


def _warn_once(ctx: PassContext, seen: set[str], key: str, message: str) -> None:
    if key in seen:
        return
    seen.add(key)
    ctx.warn("clip", message)


def _drop_group_clip(carrier: etree._Element, handled: set[etree._Element]) -> None:
    """Remove a clip-path from a group whose geometry was all trimmed."""
    if carrier.get("clip-path") is None:
        return
    for element in carrier.iterdescendants():
        if not isinstance(element.tag, str):
            continue
        if localname(element) in GEOMETRY_TAGS and element not in handled:
            return
    carrier.attrib.pop("clip-path", None)


def trim_clipped_strokes(root: etree._Element, ctx: PassContext) -> int:
    """Cut unfilled, stroked paths down to the visible part of their clip rect."""
    config = ctx.config
    trimmed = 0
    seen: set[str] = set()
    handled: set[etree._Element] = set()
    carriers: set[etree._Element] = set()

    for path in list(collect(root, ["path"])):
        parent = path.getparent()
        if parent is None:
            continue
        if inherited(path, "fill") != "none":
            continue  # filled shapes would change appearance
        stroke = inherited(path, "stroke")
        if not stroke or stroke.strip().lower() == "none":
            continue
        if _is_zero(inherited(path, "stroke-opacity")):
            continue

        carrier, clip_id = _clip_carrier(path)
        if carrier is None or clip_id is None:
            continue
        clip = _find_id(root, clip_id)
        if clip is None:
            _warn_once(
                ctx, seen, f"missing:{clip_id}", f"clipPath #{clip_id} not found, left as is"
            )
            continue
        geometry = _clip_geometry(clip)
        if geometry is None:
            _warn_once(
                ctx,
                seen,
                f"unsupported:{clip_id}",
                f"{describe(path)}: clipPath #{clip_id} is not a single axis-aligned "
                "rectangle in user space, left as is",
            )
            continue
        bounds, clip_matrix = geometry

        if any(inherited(path, name) for name in ("marker-start", "marker-mid", "marker-end")):
            _warn_once(
                ctx,
                seen,
                "markers",
                f"{describe(path)}: markers on clipped strokes, left as is",
            )
            continue
        if path.get("pathLength"):
            _warn_once(
                ctx,
                seen,
                "pathlength",
                f"{describe(path)}: pathLength would be invalidated by trimming, left as is",
            )
            continue

        dash = inherited(path, "stroke-dasharray")
        if dash and dash.strip().lower() != "none" and not config.trim_dashed:
            _warn_once(
                ctx,
                seen,
                "dash",
                f"{describe(path)}: dashed clipped strokes, left as is "
                "(--trim-dashed shifts the dash phase but cuts them)",
            )
            continue

        try:
            segments = to_segments(parse_commands(path.get("d")))
        except PathDataError as exc:
            _warn_once(ctx, seen, f"d:{id(path)}", f"{describe(path)}: {exc}")
            continue
        if any(segment[0] == "A" for segment in segments):
            _warn_once(
                ctx, seen, "arcs", f"{describe(path)}: arcs are not trimmed, left as is"
            )
            continue

        element_matrix = _matrix_to(path, carrier)
        if element_matrix is None:
            continue
        try:
            to_clip = clip_matrix.inverse() @ element_matrix
            from_clip = to_clip.inverse()
        except PathDataError as exc:
            _warn_once(ctx, seen, f"singular:{clip_id}", f"{describe(path)}: {exc}")
            continue

        local = transform_segments(segments, to_clip)
        if _all_points_inside(local, bounds):
            continue

        effective = bounds
        if config.drop_clip:
            stroke_width = parse_length(inherited(path, "stroke-width"), 1.0) or 0.0
            inset = config.clip_inset if config.clip_inset is not None else stroke_width / 2
            delta = inset * to_clip.scale
            if delta > 0:
                xmin, ymin, xmax, ymax = bounds
                effective = (xmin + delta, ymin + delta, xmax - delta, ymax - delta)
                if effective[0] >= effective[2] or effective[1] >= effective[3]:
                    _warn_once(
                        ctx,
                        seen,
                        f"inset:{clip_id}",
                        f"{describe(path)}: stroke width does not fit the clip rectangle",
                    )
                    continue

        pieces = clip_segments(local, effective)
        if pieces is None:
            continue
        if not pieces:
            if config.drop_clip:
                parent.remove(path)
                handled.add(path)
                trimmed += 1
            continue

        back = transform_segments(pieces, from_clip)
        path.set("d", format_commands(segments_to_commands(back), config.precision))
        handled.add(path)
        if config.drop_clip:
            path.attrib.pop("clip-path", None)
            carriers.add(carrier)
        trimmed += 1

    if config.drop_clip:
        for carrier in carriers:
            if carrier.getparent() is not None or carrier is root:
                _drop_group_clip(carrier, handled)

    return trimmed
