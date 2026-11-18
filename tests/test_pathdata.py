from __future__ import annotations

import math

import pytest

from svg_cleaner.pathdata import (
    Matrix,
    PathDataError,
    clip_cubic,
    clip_line,
    clip_segments,
    format_commands,
    format_number,
    parse_commands,
    parse_transform,
    round_path_data,
    segments_to_commands,
    to_segments,
)


def test_parse_and_format_roundtrip():
    commands = parse_commands("M 1 2 L 3 4 Z")
    assert [c.letter for c in commands] == ["M", "L", "Z"]
    assert format_commands(commands) == "M 1 2 L 3 4 Z"


def test_implicit_lineto_after_moveto():
    commands = parse_commands("M0 0 10 10 20 20")
    assert [c.letter for c in commands] == ["M", "L", "L"]
    assert commands[-1].values == (20.0, 20.0)


def test_quoted_style_numbers_and_exponents():
    commands = parse_commands("m1e1,-2.5.5.5")
    assert commands[0].values == (10.0, -2.5)


def test_bad_path_data_raises():
    with pytest.raises(PathDataError):
        parse_commands("M 1")
    with pytest.raises(PathDataError):
        parse_commands("L 1 1")


def test_number_formatting_drops_negative_zero():
    assert format_number(0.0004, 3) == "0"
    assert format_number(-0.0004, 3) == "0"
    assert format_number(2.5, 3) == "2.5"
    assert round_path_data("M 1.23456 2.34567 L 3 4", 2) == "M 1.23 2.35 L 3 4"


def test_shorthand_commands_are_expanded():
    segments = to_segments(parse_commands("M0 0h10v10"))
    assert segments[1] == ("L", (0.0, 0.0), (10.0, 0.0))
    assert segments[2] == ("L", (10.0, 0.0), (10.0, 10.0))

    segments = to_segments(parse_commands("M0 0 C 1 1 2 2 3 3 S 5 5 6 6"))
    assert segments[1] == ("C", (0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0))
    assert segments[2][2] == (4.0, 4.0)  # reflected control point

    segments = to_segments(parse_commands("M0 0 Q 1 1 2 0 T 4 0"))
    assert segments[2][2] == (3.0, -1.0)


def test_relative_commands_are_absolute_in_segments():
    segments = to_segments(parse_commands("m 5 5 l 10 0"))
    assert segments[0] == ("M", (5.0, 5.0))
    assert segments[1] == ("L", (5.0, 5.0), (15.0, 5.0))


def test_relative_serialization_resets_after_close():
    segments = [
        ("M", (5.0, 5.0)),
        ("L", (5.0, 5.0), (10.0, 10.0)),
        ("Z", (10.0, 10.0), (5.0, 5.0)),
        ("M", (20.0, 20.0)),
    ]
    commands = segments_to_commands(segments, mode="relative")
    assert format_commands(commands) == "m 5 5 l 5 5 z m 15 15"


def test_transform_parsing_and_composition():
    matrix = parse_transform("translate(10,20) scale(2)")
    assert matrix.apply((1.0, 1.0)) == (12.0, 22.0)

    rotate = parse_transform("rotate(90 10 10)")
    x, y = rotate.apply((10.0, 12.0))
    assert (round(x, 9), round(y, 9)) == (8.0, 10.0)

    inverse = matrix @ matrix.inverse()
    assert isinstance(inverse, Matrix)
    assert inverse.is_identity()


def test_identity_detection():
    assert parse_transform("matrix(1,0,0,1,0,0)").is_identity()
    assert not parse_transform("translate(0.5)").is_identity()


@pytest.mark.parametrize("transform", ["translate(foo)", "translate(1) junk"])
def test_invalid_transform_raises(transform):
    with pytest.raises(PathDataError):
        parse_transform(transform)


def test_line_clipping_against_box():
    assert clip_line((-5.0, 5.0), (15.0, 5.0), (0, 0, 10, 10)) == ((0, 5), (10, 5))
    assert clip_line((-5.0, 5.0), (-1.0, 5.0), (0, 0, 10, 10)) is None
    assert clip_line((20.0, 20.0), (30.0, 30.0), (0, 0, 10, 10)) is None


def test_cubic_clipping_keeps_only_the_inside_parts():
    pieces = clip_cubic((5, -5), (5, 20), (5, 20), (5, 15), (0, 0, 10, 10))
    assert pieces
    for piece in pieces:
        for x, y in piece:
            assert -1e-6 <= x <= 10 + 1e-6
            assert -1e-6 <= y <= 10 + 1e-6


def test_clip_segments_returns_none_for_arcs():
    segments = to_segments(parse_commands("M0 0 a 5 5 0 0 0 10 0"))
    assert clip_segments(segments, (0, 0, 10, 10)) is None


def test_clip_segments_keeps_only_the_intersecting_edge():
    segments = to_segments(parse_commands("M -10 5 L 20 5 L 5 30 Z"))
    clipped = clip_segments(segments, (0, 0, 10, 10))
    assert clipped == [("M", (0.0, 5.0)), ("L", (0.0, 5.0), (10.0, 5.0))]
    assert math.isclose(clipped[-1][-1][0], 10.0, abs_tol=1e-6)


def test_clip_segments_handles_missing_overlap():
    segments = to_segments(parse_commands("M -20 -20 L -10 -10"))
    clipped = clip_segments(segments, (0, 0, 10, 10))
    assert clipped == []
