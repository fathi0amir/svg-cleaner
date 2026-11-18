"""Tests for the Bokeh rectangular clip trimming pass."""

from __future__ import annotations

import pytest

from svg_cleaner.config import CleanConfig
from svg_cleaner.document import load_svg, localname
from svg_cleaner.passes import PassContext
from svg_cleaner.passes.clip import trim_clipped_strokes
from svg_cleaner.pathdata import parse_commands, to_segments

SVG = "http://www.w3.org/2000/svg"

CLIPPED = f"""<svg xmlns="{SVG}" viewBox="0 0 100 100">
  <defs>
    <clipPath id="plot-area"><rect x="0" y="0" width="50" height="50"/></clipPath>
  </defs>
  <g clip-path="url(#plot-area)">
    <path d="M -20 25 L 80 25" fill="none" stroke="black"/>
  </g>
</svg>"""


def context(**kwargs) -> PassContext:
    return PassContext(config=CleanConfig(**kwargs), profile="bokeh")


def find(root, name, **attrs):
    for element in root.iter():
        if isinstance(element.tag, str) and localname(element) == name:
            if all(element.get(key) == value for key, value in attrs.items()):
                return element
    raise AssertionError(f"no <{name}> with {attrs}")


def points_of(data: str) -> list[tuple[float, float]]:
    points = []
    for segment in to_segments(parse_commands(data)):
        for item in segment[1:]:
            if isinstance(item, tuple) and len(item) == 2 and all(
                isinstance(value, float) for value in item
            ):
                points.append(item)
    return points


def test_clip_on_ancestor_group_trims_a_straight_stroke():
    root = load_svg(CLIPPED)
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 1
    path = find(root, "path")
    assert path.get("d") == "M 0 25 L 50 25"
    assert path.get("clip-path") is None  # it was on the group
    assert find(root, "g").get("clip-path") == "url(#plot-area)"


def test_direct_clip_is_trimmed_and_reference_kept_by_default():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><path d="M 10 10 L 40 10 L 40 40 L 10 40 Z"/></clipPath>
          <path d="M -50 20 L 90 20" fill="none" stroke="#f00" clip-path="url(#c)"/>
        </svg>"""
    )
    assert trim_clipped_strokes(root, context()) == 1
    path = find(root, "path", fill="none")
    assert path.get("d") == "M 10 20 L 40 20"
    assert path.get("clip-path") == "url(#c)"


def test_drop_clip_insets_by_half_the_stroke_width():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><rect width="50" height="50"/></clipPath>
          <path d="M -20 25 L 80 25" fill="none" stroke="black" stroke-width="4"
                clip-path="url(#c)"/>
        </svg>"""
    )
    assert trim_clipped_strokes(root, context(drop_clip=True)) == 1
    path = find(root, "path")
    assert path.get("clip-path") is None
    assert path.get("d") == "M 2 25 L 48 25"


def test_curved_strokes_are_subdivided_and_stay_inside():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><rect width="50" height="50"/></clipPath>
          <g clip-path="url(#c)">
            <path d="M 0 10 C 0 100 50 100 50 10" fill="none" stroke="black"/>
          </g>
        </svg>"""
    )
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 1
    data = find(root, "path").get("d")
    assert "C" in data
    for x, y in points_of(data):
        assert -1e-6 <= x <= 50 + 1e-6
        assert -1e-6 <= y <= 50 + 1e-6


def test_filled_shapes_are_never_trimmed():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><rect width="50" height="50"/></clipPath>
          <g clip-path="url(#c)">
            <path d="M -20 25 L 80 25" fill="black" stroke="black"/>
          </g>
        </svg>"""
    )
    original = find(root, "path").get("d")
    assert trim_clipped_strokes(root, context()) == 0
    assert find(root, "path").get("d") == original


def test_arcs_are_left_untouched_with_a_warning():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><rect width="50" height="50"/></clipPath>
          <g clip-path="url(#c)">
            <path d="M -20 25 A 5 5 0 0 0 80 25" fill="none" stroke="black"/>
          </g>
        </svg>"""
    )
    original = find(root, "path").get("d")
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 0
    assert find(root, "path").get("d") == original
    assert any("arc" in warning for warning in ctx.warnings)


def test_object_bounding_box_clips_are_rejected():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c" clipPathUnits="objectBoundingBox">
            <rect width="0.5" height="0.5"/>
          </clipPath>
          <path d="M -20 25 L 80 25" fill="none" stroke="black" clip-path="url(#c)"/>
        </svg>"""
    )
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 0
    assert any("axis-aligned rectangle" in warning for warning in ctx.warnings)


def test_rounded_rect_and_multi_shape_clips_are_rejected():
    for shape in (
        '<rect width="10" height="10" rx="2"/>',
        '<rect width="10" height="10"/><rect x="20" width="10" height="10"/>',
    ):
        root = load_svg(
            f'<svg xmlns="{SVG}"><clipPath id="c">{shape}</clipPath>'
            '<path d="M -20 25 L 80 25" fill="none" stroke="black" clip-path="url(#c)"/></svg>'
        )
        ctx = context()
        assert trim_clipped_strokes(root, ctx) == 0
        assert ctx.warnings


def test_transformed_paths_are_clipped_in_clip_space():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
          <clipPath id="c"><rect x="0" y="0" width="10" height="10"/></clipPath>
          <g clip-path="url(#c)" transform="translate(100,0)">
            <path d="M -200 2 L 200 2" fill="none" stroke="black" transform="scale(2)"/>
          </g>
        </svg>"""
    )
    assert trim_clipped_strokes(root, context()) == 1
    path = find(root, "path")
    # scale(2) maps the stroke to y=4 and x=-400..400; only x 0..10 of the clip
    # is visible, which maps back to x 0..5 in the path's own coordinates
    assert path.get("d") == "M 0 2 L 5 2"


def test_dashed_and_marked_strokes_are_skipped_by_default():
    for extra in ('stroke-dasharray="2 2"', 'marker-end="url(#arrow)"'):
        root = load_svg(
            f'<svg xmlns="{SVG}"><clipPath id="c"><rect width="10" height="10"/></clipPath>'
            f'<path d="M -20 5 L 80 5" fill="none" stroke="black" {extra} '
            'clip-path="url(#c)"/></svg>'
        )
        original = find(root, "path").get("d")
        ctx = context()
        assert trim_clipped_strokes(root, ctx) == 0
        assert find(root, "path").get("d") == original
        assert ctx.warnings


def test_dashed_strokes_are_trimmed_when_opted_in():
    root = load_svg(
        f'<svg xmlns="{SVG}"><clipPath id="c"><rect width="10" height="10"/></clipPath>'
        '<path d="M -20 5 L 80 5" fill="none" stroke="black" stroke-dasharray="2 2" '
        'clip-path="url(#c)"/></svg>'
    )
    assert trim_clipped_strokes(root, context(trim_dashed=True)) == 1
    assert find(root, "path").get("d") == "M 0 5 L 10 5"


def test_paths_with_path_length_are_left_alone():
    root = load_svg(
        f'<svg xmlns="{SVG}"><clipPath id="c"><rect width="10" height="10"/></clipPath>'
        '<path d="M -20 5 L 80 5" fill="none" stroke="black" pathLength="100" '
        'clip-path="url(#c)"/></svg>'
    )
    original = find(root, "path").get("d")
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 0
    assert find(root, "path").get("d") == original
    assert any("pathLength" in warning for warning in ctx.warnings)


def test_no_clip_means_no_work():
    root = load_svg(
        f'<svg xmlns="{SVG}"><path d="M 0 0 L 10 10" fill="none" stroke="black"/></svg>'
    )
    ctx = context()
    assert trim_clipped_strokes(root, ctx) == 0
    assert not ctx.warnings


@pytest.mark.parametrize("precision", [3, 2])
def test_clip_is_idempotent(precision):
    root = load_svg(CLIPPED)
    ctx = context(precision=precision)
    trim_clipped_strokes(root, ctx)
    first = find(root, "path").get("d")
    trim_clipped_strokes(root, ctx)
    assert find(root, "path").get("d") == first
