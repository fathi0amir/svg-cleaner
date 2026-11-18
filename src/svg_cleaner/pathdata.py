"""Path data parsing, serialization, affine transforms and clipping.

The module deliberately avoids ``svgpathtools`` (and therefore numpy/scipy):
paths are parsed into plain tuples so that the clip pass can split curves
exactly and every other pass can re-round numbers without changing geometry.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence

Point = tuple[float, float]
Bounds = tuple[float, float, float, float]

__all__ = [
    "PathDataError",
    "Command",
    "Matrix",
    "parse_commands",
    "format_commands",
    "format_number",
    "round_path_data",
    "to_segments",
    "segments_to_commands",
    "parse_transform",
    "transform_segments",
    "clip_line",
    "clip_segments",
    "is_identity",
]


class PathDataError(ValueError):
    """The ``d`` attribute cannot be parsed."""


CMD_ARITY = {
    "M": 2,
    "L": 2,
    "H": 1,
    "V": 1,
    "C": 6,
    "S": 4,
    "Q": 4,
    "T": 2,
    "A": 7,
    "Z": 0,
}

_NUMBER = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
_TOKEN_RE = re.compile(rf"(?P<cmd>[MmZzLlHhVvCcSsQqTtAa])|(?P<num>{_NUMBER})")
_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")


@dataclass(frozen=True)
class Command:
    letter: str
    values: tuple[float, ...] = ()

    @property
    def absolute(self) -> str:
        return self.letter.upper()


# -- numbers ------------------------------------------------------------------


def format_number(value: float, precision: int = 3) -> str:
    if not math.isfinite(value):
        raise PathDataError("path data contains a non-finite number")
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_commands(commands: Sequence[Command], precision: int = 3) -> str:
    parts: list[str] = []
    for command in commands:
        if command.values:
            numbers = " ".join(format_number(value, precision) for value in command.values)
            parts.append(f"{command.letter} {numbers}")
        else:
            parts.append(command.letter)
    return " ".join(parts)


# -- parsing ------------------------------------------------------------------


def _implicit(letter: str) -> str:
    return "L" if letter == "M" else "l" if letter == "m" else letter


def parse_commands(d: Optional[str]) -> list[Command]:
    """Parse a ``d`` attribute, preserving the letters as written."""
    if d is None:
        raise PathDataError("empty path data")
    commands: list[Command] = []
    pos = 0
    length = len(d)
    while pos < length:
        char = d[pos]
        if char.isspace() or char == ",":
            pos += 1
            continue
        match = _TOKEN_RE.match(d, pos)
        if match is None:
            raise PathDataError(f"unexpected character {char!r} at offset {pos}")
        if match.lastgroup != "cmd":
            raise PathDataError(f"number without a command at offset {pos}")
        letter = match.group("cmd")
        pos = match.end()
        arity = CMD_ARITY[letter.upper()]
        if arity == 0:
            commands.append(Command(letter))
            continue
        if not commands and letter not in "Mm":
            raise PathDataError("path data must start with a moveto")
        first = True
        while True:
            values: list[float] = []
            for _ in range(arity):
                while pos < length and (d[pos].isspace() or d[pos] == ","):
                    pos += 1
                number = _TOKEN_RE.match(d, pos)
                if number is None or number.lastgroup != "num":
                    raise PathDataError(f"'{letter}' needs {arity} numbers")
                values.append(float(number.group("num")))
                pos = number.end()
            commands.append(Command(letter if first else _implicit(letter), tuple(values)))
            first = False
            look = pos
            while look < length and (d[look].isspace() or d[look] == ","):
                look += 1
            following = _TOKEN_RE.match(d, look)
            if following is None or following.lastgroup != "num":
                break
            pos = look
    if not commands:
        raise PathDataError("empty path data")
    return commands


def round_path_data(d: Optional[str], precision: int = 3) -> str:
    """Round the numbers in a path without touching its commands."""
    return format_commands(parse_commands(d), precision)


# -- segments -----------------------------------------------------------------
# ("M", p)
# ("L", p0, p1)
# ("C", p0, c1, c2, p1)
# ("Q", p0, c1, p1)
# ("A", p0, (rx, ry, phi, large, sweep), p1)
# ("Z", p0, p1)
Segment = tuple


def _reflect(point: Point, current: Point) -> Point:
    return (2 * current[0] - point[0], 2 * current[1] - point[1])


def to_segments(commands: Sequence[Command]) -> list[Segment]:
    """Normalize a command list to absolute M/L/C/Q/A/Z segments."""
    segments: list[Segment] = []
    current: Point = (0.0, 0.0)
    subpath_start: Point = (0.0, 0.0)
    last_control: Optional[Point] = None
    last_letter = ""
    for command in commands:
        letter = command.letter
        code = letter.upper()
        relative = letter.islower()
        values = command.values

        def point(index: int) -> Point:
            x, y = values[index], values[index + 1]
            return (current[0] + x, current[1] + y) if relative else (x, y)

        if code == "M":
            target = point(0)
            segments.append(("M", target))
            current = subpath_start = target
            last_control = None
        elif code == "L":
            target = point(0)
            segments.append(("L", current, target))
            current = target
            last_control = None
        elif code == "H":
            x = values[0] + (current[0] if relative else 0.0)
            target = (x, current[1])
            segments.append(("L", current, target))
            current = target
            last_control = None
        elif code == "V":
            y = values[0] + (current[1] if relative else 0.0)
            target = (current[0], y)
            segments.append(("L", current, target))
            current = target
            last_control = None
        elif code == "C":
            c1, c2, end = point(0), point(2), point(4)
            segments.append(("C", current, c1, c2, end))
            current, last_control = end, c2
        elif code == "S":
            c1 = _reflect(last_control, current) if last_letter in "CcSs" and last_control else current
            c2, end = point(0), point(2)
            segments.append(("C", current, c1, c2, end))
            current, last_control = end, c2
        elif code == "Q":
            c1, end = point(0), point(2)
            segments.append(("Q", current, c1, end))
            current, last_control = end, c1
        elif code == "T":
            c1 = _reflect(last_control, current) if last_letter in "QqTt" and last_control else current
            end = point(0)
            segments.append(("Q", current, c1, end))
            current, last_control = end, c1
        elif code == "A":
            rx, ry = abs(values[0]), abs(values[1])
            phi = values[2]
            large, sweep = bool(values[3]), bool(values[4])
            end = point(5)
            if rx == 0 or ry == 0:
                segments.append(("L", current, end))
            else:
                segments.append(("A", current, (rx, ry, phi, large, sweep), end))
            current = end
            last_control = None
        elif code == "Z":
            segments.append(("Z", current, subpath_start))
            current = subpath_start
            last_control = None
        last_letter = letter
    return segments


def _segments_to_absolute_commands(segments: Sequence[Segment]) -> list[Command]:
    commands: list[Command] = []
    for segment in segments:
        kind = segment[0]
        if kind == "M":
            commands.append(Command("M", segment[1]))
        elif kind == "L":
            commands.append(Command("L", segment[2]))
        elif kind == "C":
            commands.append(Command("C", segment[1] + segment[2] + segment[3]))
        elif kind == "Q":
            commands.append(Command("Q", segment[1] + segment[2]))
        elif kind == "A":
            rx, ry, phi, large, sweep = segment[2]
            commands.append(
                Command("A", (rx, ry, phi, float(large), float(sweep)) + segment[3])
            )
        elif kind == "Z":
            commands.append(Command("Z"))
    return commands


def _to_relative(commands: Sequence[Command]) -> list[Command]:
    result: list[Command] = []
    current: Point = (0.0, 0.0)
    subpath_start: Point = (0.0, 0.0)
    for command in commands:
        code = command.absolute
        values = command.values
        if code == "Z":
            result.append(Command("z"))
            # the current point after a close is the subpath start
            current = subpath_start
            continue
        if code == "M":
            end = (values[0], values[1])
            result.append(Command("m", (end[0] - current[0], end[1] - current[1])))
            current = end
            subpath_start = end
        elif code == "L":
            end = (values[0], values[1])
            result.append(Command("l", (end[0] - current[0], end[1] - current[1])))
            current = end
        elif code == "C":
            points = [(values[i], values[i + 1]) for i in (0, 2, 4)]
            deltas = [(p[0] - current[0], p[1] - current[1]) for p in points]
            result.append(Command("c", deltas[0] + deltas[1] + deltas[2]))
            current = points[2]
        elif code == "Q":
            points = [(values[0], values[1]), (values[2], values[3])]
            deltas = [(p[0] - current[0], p[1] - current[1]) for p in points]
            result.append(Command("q", deltas[0] + deltas[1]))
            current = points[1]
        elif code == "A":
            end = (values[5], values[6])
            result.append(
                Command(
                    "a",
                    (values[0], values[1], values[2], values[3], values[4],
                     end[0] - current[0], end[1] - current[1]),
                )
            )
            current = end
    return result


def segments_to_commands(segments: Sequence[Segment], mode: str = "absolute") -> list[Command]:
    """Turn segments into commands; ``mode`` is ``absolute`` or ``relative``."""
    commands = _segments_to_absolute_commands(segments)
    if mode == "relative":
        return _to_relative(commands)
    return commands


# -- affine transforms --------------------------------------------------------


@dataclass(frozen=True)
class Matrix:
    """2D affine matrix ``[[a, c, e], [b, d, f]]``."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, point: Point) -> Point:
        x, y = point
        return (self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f)

    def __matmul__(self, other: Matrix) -> Matrix:
        """``self @ other``: apply *other* first, then *self*."""
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.e + self.c * other.f + self.e,
            self.b * other.e + self.d * other.f + self.f,
        )

    def inverse(self) -> Matrix:
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            raise PathDataError("transform is singular and cannot be inverted")
        return Matrix(
            self.d / det,
            -self.b / det,
            -self.c / det,
            self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )

    @property
    def scale(self) -> float:
        det = abs(self.a * self.d - self.b * self.c)
        return math.sqrt(det) if det > 0 else 0.0

    def is_identity(self, tolerance: float = 1e-9) -> bool:
        return (
            abs(self.a - 1) <= tolerance
            and abs(self.b) <= tolerance
            and abs(self.c) <= tolerance
            and abs(self.d - 1) <= tolerance
            and abs(self.e) <= tolerance
            and abs(self.f) <= tolerance
        )


IDENTITY = Matrix()


def parse_transform(text: Optional[str]) -> Matrix:
    """Parse an SVG ``transform`` attribute; unparsable parts are ignored."""
    if not text:
        return IDENTITY
    result = IDENTITY
    for match in _TRANSFORM_RE.finditer(text):
        name = match.group(1).lower()
        args = [float(part) for part in re.split(r"[\s,]+", match.group(2).strip()) if part]
        try:
            if name == "translate" and args:
                tx = args[0]
                ty = args[1] if len(args) > 1 else 0.0
                matrix = Matrix(1, 0, 0, 1, tx, ty)
            elif name == "scale" and args:
                sx = args[0]
                sy = args[1] if len(args) > 1 else sx
                matrix = Matrix(sx, 0, 0, sy, 0, 0)
            elif name == "rotate" and args:
                angle = math.radians(args[0])
                cos, sin = math.cos(angle), math.sin(angle)
                rotation = Matrix(cos, sin, -sin, cos)
                if len(args) >= 3:
                    cx, cy = args[1], args[2]
                    matrix = (
                        Matrix(1, 0, 0, 1, cx, cy) @ rotation @ Matrix(1, 0, 0, 1, -cx, -cy)
                    )
                else:
                    matrix = rotation
            elif name == "skewx" and args:
                matrix = Matrix(1, 0, math.tan(math.radians(args[0])), 1)
            elif name == "skewy" and args:
                matrix = Matrix(1, math.tan(math.radians(args[0])), 0, 1)
            elif name == "matrix" and len(args) >= 6:
                matrix = Matrix(*args[:6])
            else:
                continue
        except (ValueError, OverflowError):
            continue
        result = result @ matrix
    return result


def transform_segments(segments: Sequence[Segment], matrix: Matrix) -> list[Segment]:
    """Apply an affine matrix to every point of every segment."""
    if matrix.is_identity():
        return list(segments)
    moved: list[Segment] = []
    rotation = math.degrees(math.atan2(matrix.b, matrix.a))
    scale = matrix.scale
    for segment in segments:
        kind = segment[0]
        if kind == "M":
            moved.append(("M", matrix.apply(segment[1])))
        elif kind == "L":
            moved.append(("L", matrix.apply(segment[1]), matrix.apply(segment[2])))
        elif kind == "C":
            moved.append(
                (
                    "C",
                    matrix.apply(segment[1]),
                    matrix.apply(segment[2]),
                    matrix.apply(segment[3]),
                    matrix.apply(segment[4]),
                )
            )
        elif kind == "Q":
            moved.append(
                (
                    "Q",
                    matrix.apply(segment[1]),
                    matrix.apply(segment[2]),
                    matrix.apply(segment[3]),
                )
            )
        elif kind == "A":
            rx, ry, phi, large, sweep = segment[2]
            moved.append(
                (
                    "A",
                    matrix.apply(segment[1]),
                    (rx * scale, ry * scale, phi + rotation, large, sweep),
                    matrix.apply(segment[3]),
                )
            )
        else:  # Z
            moved.append(("Z", matrix.apply(segment[1]), matrix.apply(segment[2])))
    return moved


def is_identity(matrix: Matrix, tolerance: float = 1e-9) -> bool:
    return matrix.is_identity(tolerance)


# -- clipping -----------------------------------------------------------------


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _inside(point: Point, bounds: Bounds, tolerance: float = 1e-9) -> bool:
    xmin, ymin, xmax, ymax = bounds
    return (
        xmin - tolerance <= point[0] <= xmax + tolerance
        and ymin - tolerance <= point[1] <= ymax + tolerance
    )


def clip_line(p0: Point, p1: Point, bounds: Bounds) -> Optional[tuple[Point, Point]]:
    """Liang-Barsky line clipping; ``None`` when the line misses the box."""
    xmin, ymin, xmax, ymax = bounds
    if xmin > xmax or ymin > ymax:
        return None
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return None
            if t < t1:
                t1 = t
    if t0 > t1:
        return None
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def _outside_same_side(points: Sequence[Point], bounds: Bounds) -> bool:
    xmin, ymin, xmax, ymax = bounds
    return (
        all(point[0] < xmin for point in points)
        or all(point[0] > xmax for point in points)
        or all(point[1] < ymin for point in points)
        or all(point[1] > ymax for point in points)
    )


def split_cubic(
    p0: Point, p1: Point, p2: Point, p3: Point, t: float = 0.5
) -> tuple[tuple[Point, Point, Point, Point], tuple[Point, Point, Point, Point]]:
    """de Casteljau subdivision."""
    a = _lerp(p0, p1, t)
    b = _lerp(p1, p2, t)
    c = _lerp(p2, p3, t)
    d = _lerp(a, b, t)
    e = _lerp(b, c, t)
    f = _lerp(d, e, t)
    return (p0, a, d, f), (f, e, c, p3)


def clip_cubic(
    p0: Point,
    p1: Point,
    p2: Point,
    p3: Point,
    bounds: Bounds,
    depth: int = 18,
) -> list[tuple[Point, Point, Point, Point]]:
    """Split a cubic into the pieces inside *bounds* (recursive subdivision)."""
    points = (p0, p1, p2, p3)
    if all(_inside(point, bounds) for point in points):
        return [points]
    if _outside_same_side(points, bounds):
        return []
    if depth <= 0:
        clipped = clip_line(p0, p3, bounds)
        if clipped is None:
            return []
        start, end = clipped
        return [(start, _lerp(start, end, 1 / 3), _lerp(start, end, 2 / 3), end)]
    left, right = split_cubic(p0, p1, p2, p3)
    return clip_cubic(*left, bounds, depth - 1) + clip_cubic(*right, bounds, depth - 1)


def _quad_to_cubic(p0: Point, c: Point, p1: Point) -> tuple[Point, Point, Point, Point]:
    c1 = (p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1]))
    c2 = (p1[0] + 2 / 3 * (c[0] - p1[0]), p1[1] + 2 / 3 * (c[1] - p1[1]))
    return p0, c1, c2, p1


def _near(a: Point, b: Point, tolerance: float = 1e-7) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def clip_segments(segments: Sequence[Segment], bounds: Bounds) -> Optional[list[Segment]]:
    """Clip a segment list to an axis-aligned box.

    Returns ``None`` when the path contains an arc (or another feature the
    clipper cannot reproduce exactly); callers must then leave it untouched.
    """
    if not segments:
        return []
    if any(segment[0] == "A" for segment in segments):
        return None

    output: list[Segment] = []
    last_end: Optional[Point] = None
    modified_subpath = False
    subpath_start: Optional[Point] = None

    def emit(segment: Segment) -> None:
        nonlocal last_end
        start = segment[1]
        if last_end is None or not _near(last_end, start):
            output.append(("M", start))
        output.append(segment)
        last_end = segment[-1]

    for segment in segments:
        kind = segment[0]
        if kind == "M":
            last_end = None
            modified_subpath = False
            subpath_start = segment[1]
            continue
        if kind in ("L", "Z"):
            p0, p1 = segment[1], segment[-1]
            if _inside(p0, bounds) and _inside(p1, bounds):
                if kind == "Z" and not modified_subpath:
                    output.append(("Z", p0, p1))
                    last_end = p1
                    continue
                emit(("L", p0, p1))
                continue
            clipped = clip_line(p0, p1, bounds)
            if clipped is not None:
                emit(("L", clipped[0], clipped[1]))
            modified_subpath = True
            last_end = None if clipped is None else last_end
            continue
        if kind == "C":
            pieces = clip_cubic(segment[1], segment[2], segment[3], segment[4], bounds)
        elif kind == "Q":
            pieces = clip_cubic(*_quad_to_cubic(segment[1], segment[2], segment[3]), bounds)
        else:  # pragma: no cover - defensive
            return None
        whole = (
            len(pieces) == 1
            and _near(pieces[0][0], segment[1])
            and _near(pieces[0][3], segment[-1])
        )
        for piece in pieces:
            emit(("C", piece[0], piece[1], piece[2], piece[3]))
        if not whole:
            modified_subpath = True
        if not pieces:
            last_end = None
    # drop a trailing moveto that leads nowhere
    while output and output[-1][0] == "M":
        output.pop()
    return output
