"""Tests for the structural, style, id, geometry and visibility passes."""

from __future__ import annotations

import pytest

from svg_cleaner import clean
from svg_cleaner.config import CleanConfig, load_config_file
from svg_cleaner.document import load_svg, localname, parse_style
from svg_cleaner.errors import SvgCleanerError
from svg_cleaner.passes import PassContext, geometry, ids, structure, styles, visibility

SVG = "http://www.w3.org/2000/svg"


def context(**kwargs) -> PassContext:
    return PassContext(config=CleanConfig(**kwargs), profile="generic")


def find(root, name, **attrs):
    for element in root.iter():
        if not isinstance(element.tag, str) or localname(element) != name:
            continue
        if all(element.get(key) == value for key, value in attrs.items()):
            return element
    raise AssertionError(f"no <{name}> with {attrs}")


def test_structure_removes_editor_junk_but_keeps_titles():
    root = load_svg(
        f"""<svg xmlns="{SVG}" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
                      xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd">
            <metadata><rdf/></metadata>
            <!-- exported -->
            <title>keep me</title>
            <sodipodi:namedview pagecolor="#fff"/>
            <g/>
            <g><rect width="1" height="1" inkscape:label="Rect"/></g>
            <g id="wrapper"><rect width="1" height="1"/></g>
        </svg>"""
    )
    removed = structure.cleanup_document(root, context())
    assert removed > 0
    assert not [element for element in root.iter() if localname(element) == "metadata"]
    assert find(root, "title").text == "keep me"
    assert not any(
        "inkscape" in key
        for element in root.iter()
        if isinstance(element.tag, str)
        for key in element.attrib
    )
    # the empty wrapper is gone, the two single-child groups survive cleanup
    groups = [element for element in root.iter() if localname(element) == "g"]
    assert [group.get("id") for group in groups] == [None, "wrapper"]

    collapsed = structure.collapse_groups(root, context())
    assert collapsed == 1
    assert find(root, "rect").getparent().tag == f"{{{SVG}}}svg"


def test_css_inlining_respects_cascade_and_inline_style():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
            <style>
              .box {{ fill: red; stroke-width: 2 }}
              rect {{ stroke: blue }}
              #special {{ stroke: green }}
              .important {{ fill: black !important }}
            </style>
            <rect id="special" class="box" width="1" height="1"/>
            <rect class="box important" width="2" height="2" style="stroke-width: 5"/>
        </svg>"""
    )
    inlined = styles.inline_css(root, context())
    assert inlined == 2
    special = find(root, "rect", id="special")
    assert parse_style(special.get("style")) == {
        "fill": "red",
        "stroke-width": "2",
        "stroke": "green",
    }
    other = find(root, "rect", width="2")
    assert parse_style(other.get("style"))["fill"] == "black !important"
    assert parse_style(other.get("style"))["stroke-width"] == "5"
    assert not [element for element in root.iter() if localname(element) == "style"]


def test_unmatched_stylesheet_is_kept():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>{{ fill: red }}</style>'
        '<rect width="1" height="1"/></svg>'
    )
    ctx = context()
    styles.inline_css(root, ctx)
    assert [element for element in root.iter() if localname(element) == "style"]
    assert any("kept" in warning and "stylesheet" in warning for warning in ctx.warnings)


def test_default_attributes_respect_inheritance():
    root = load_svg(
        f'<svg xmlns="{SVG}"><g stroke-width="3" fill-opacity="0.5">'
        '<path d="M0 0 L1 1" fill="none" stroke="black" stroke-width="1" '
        'fill-opacity="1"/></g></svg>'
    )
    styles.drop_default_attributes(root, context())
    path = find(root, "path")
    # dropping these would make the path inherit 3 / 0.5 from the group
    assert path.get("stroke-width") == "1"
    assert path.get("fill-opacity") == "1"


def test_standalone_default_attributes_are_still_dropped():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect width="1" height="1" stroke-width="1"/></svg>'
    )
    assert styles.drop_default_attributes(root, context()) == 1
    assert find(root, "rect").get("stroke-width") is None


def test_important_stylesheet_rule_beats_normal_inline_style():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>rect {{ fill: red !important }}</style>'
        '<rect width="1" height="1" style="fill: blue"/></svg>'
    )
    styles.inline_css(root, context())
    assert parse_style(find(root, "rect").get("style"))["fill"] == "red !important"


def test_important_inline_rule_beats_important_stylesheet_rule():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>rect {{ fill: red !important }}</style>'
        '<rect width="1" height="1" style="fill: blue !important"/></svg>'
    )
    styles.inline_css(root, context())
    assert parse_style(find(root, "rect").get("style"))["fill"] == "blue !important"


def test_unsupported_css_keeps_stylesheet():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>:root {{ --color: red }} '
        "rect { fill: var(--color) } circle { fill: blue }</style>"
        '<rect width="1" height="1"/><circle r="1"/></svg>'
    )
    ctx = context()
    styles.inline_css(root, ctx)
    assert [element for element in root.iter() if localname(element) == "style"]
    assert ctx.warnings
    assert find(root, "circle").get("style") is None


def test_dynamic_css_keeps_entire_stylesheet_unchanged():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>.a:hover {{ fill: red }} '
        ".b { fill: blue }</style>"
        '<rect class="a" width="1" height="1"/>'
        '<rect class="b" width="1" height="1"/></svg>'
    )
    ctx = context()
    original = "".join(root[0].itertext())
    styles.inline_css(root, ctx)
    assert "".join(root[0].itertext()) == original
    assert all(
        element.get("style") is None
        for element in root.iter()
        if localname(element) == "rect"
    )
    assert ctx.warnings


def test_skipped_css_property_keeps_entire_stylesheet():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>.a {{ animation: x 1s }} '
        ".b { fill: blue }</style>"
        '<rect class="a" width="1" height="1"/>'
        '<rect class="b" width="1" height="1"/></svg>'
    )
    ctx = context()
    styles.inline_css(root, ctx)
    assert [element for element in root.iter() if localname(element) == "style"]
    assert all(
        element.get("style") is None
        for element in root.iter()
        if localname(element) == "rect"
    )
    assert ctx.warnings


def test_scripts_keep_stylesheets_and_ids_unchanged():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>rect {{ fill: blue }}</style>'
        '<rect id="longIdentifier" width="1" height="1"/>'
        "<script>addDynamicNode()</script></svg>"
    )
    ctx = context()
    styles.inline_css(root, ctx)
    ids.cleanup_ids(root, ctx)
    assert [element for element in root.iter() if localname(element) == "style"]
    assert find(root, "rect").get("style") is None
    assert find(root, "rect").get("id") == "longIdentifier"


def test_config_rejects_wrong_types(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('[tool.svg-cleaner]\nprecision = "3"\n', encoding="utf-8")
    with pytest.raises(SvgCleanerError, match="precision must be an integer"):
        load_config_file(config)


def test_style_to_attributes_and_defaults():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
            <rect style="fill: red; stroke-width: 1; cursor: pointer" width="1" height="1"/>
            <g style="fill: blue"/>
        </svg>"""
    )
    ctx = context()
    styles.style_to_attributes(root, ctx)
    rect = find(root, "rect")
    assert rect.get("fill") == "red"
    assert rect.get("stroke-width") == "1"
    assert "cursor" in rect.get("style")  # unknown property stays in style
    styles.drop_default_attributes(root, ctx)
    assert rect.get("stroke-width") is None
    assert find(root, "g").get("fill") == "blue"


def test_style_to_attributes_preserves_important_declarations():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect style="fill: red !important" width="1" height="1"/></svg>'
    )
    styles.style_to_attributes(root, context())
    rect = find(root, "rect")
    assert rect.get("fill") is None
    assert rect.get("style") == "fill: red !important"


def test_cleanup_ids_shortens_and_rewrites_references():
    root = load_svg(
        f"""<svg xmlns="{SVG}" id="root-id">
            <defs>
              <linearGradient id="longGradientIdentifier"><stop offset="0"/></linearGradient>
              <clipPath id="longClipIdentifier"><rect width="10" height="10"/></clipPath>
              <linearGradient id="unusedGradientIdentifier"><stop offset="0"/></linearGradient>
            </defs>
            <rect width="10" height="10" fill="url(#longGradientIdentifier)"
                  clip-path="url(#longClipIdentifier)"/>
            <use href="#longGradientIdentifier"/>
        </svg>"""
    )
    ctx = context()
    changed = ids.cleanup_ids(root, ctx)
    assert changed > 0
    gradient = find(root, "linearGradient", id="a")
    clip = find(root, "clipPath", id="b")
    assert root.get("id") == "root-id"
    rect = find(root, "rect", fill="url(#a)")
    assert rect.get("fill") == "url(#a)"
    assert rect.get("clip-path") == "url(#b)"
    assert find(root, "use").get("href") == "#a"
    unused = [
        element
        for element in root.iter(f"{{{SVG}}}linearGradient")
        if element.get("id") is None
    ]
    assert len(unused) == 1
    assert gradient is not clip


def test_smil_timing_references_keep_their_ids():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect id="target" width="1" height="1"/>'
        '<rect width="2" height="2"><animate attributeName="x" begin="target.click; 2s" '
        'dur="1s"/></rect></svg>'
    )
    assert "target" in ids.referenced_ids(root)
    ids.cleanup_ids(root, context())
    renamed = find(root, "rect", width="1").get("id")
    assert renamed is not None
    # shortening must rewrite the timing reference as well
    assert find(root, "animate").get("begin") == f"{renamed}.click; 2s"


def test_clean_only_and_skip():
    svg = (
        f'<svg xmlns="{SVG}"><rect width="1" height="1" display="none"/>'
        '<rect width="2" height="2"/></svg>'
    )
    result = clean(svg, CleanConfig(), skip=["visibility"])
    assert 'display="none"' in result.svg
    result2 = clean(svg, CleanConfig(), only=["structure"])
    assert 'display="none"' in result2.svg


def test_cleanup_ids_bails_out_when_a_stylesheet_remains():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>#longIdentifier{{fill:red}}</style>'
        '<rect id="longIdentifier" width="1" height="1"/></svg>'
    )
    ctx = context()
    assert ids.cleanup_ids(root, ctx) == 0
    assert ctx.warnings and "style" in ctx.warnings[0]


def test_geometry_rounds_numbers_and_drops_identity_transforms():
    root = load_svg(
        f"""<svg xmlns="{SVG}" viewBox="0.0004 0 10.50004 10.99996">
            <rect x="1.23456" y="2.34567" width="10.0004" height="10" transform="translate(0,0)"/>
            <path d="M 1.23456 2.34567 L 3 4" stroke-width="0.5" transform="matrix(1,0,0,1,0,0)"/>
            <circle cx="5" cy="5" r="2" transform="scale(1)"/>
        </svg>"""
    )
    ctx = context(precision=2)
    geometry.simplify_geometry(root, ctx)
    rect = find(root, "rect")
    assert rect.get("x") == "1.23"
    assert rect.get("y") == "2.35"
    assert rect.get("width") == "10"
    assert rect.get("transform") is None
    path = find(root, "path")
    assert path.get("d") == "M 1.23 2.35 L 3 4"
    assert path.get("transform") is None
    assert root.get("viewBox") == "0 0 10.5 11"

    strict_precision = context(precision=0)
    geometry.simplify_geometry(root, strict_precision)
    assert find(root, "path").get("stroke-width") == "0.5"  # never collapses to 0


def test_precision_never_collapses_viewbox_or_dashes_to_zero():
    root = load_svg(
        f'<svg xmlns="{SVG}" viewBox="0 0 0.0004 10">'
        '<path d="M0 0 L1 1" stroke-dasharray="0.0004 2"/></svg>'
    )
    geometry.simplify_geometry(root, context(precision=2))
    assert root.get("viewBox") == "0 0 0.0004 10"
    assert find(root, "path").get("stroke-dasharray") == "0.0004 2"


def test_precision_never_collapses_opacity_to_zero():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect opacity="0.0004" fill-opacity="0.0004" '
        'width="1" height="1"/></svg>'
    )
    geometry.simplify_geometry(root, context(precision=2))
    rect = find(root, "rect")
    assert rect.get("opacity") == "0.0004"
    assert rect.get("fill-opacity") == "0.0004"


def test_path_mode_relative_rewrites_commands():
    root = load_svg(
        f'<svg xmlns="{SVG}"><path d="M 0 0 L 10 10 L 20 0"/></svg>'
    )
    geometry.simplify_geometry(root, context(path_mode="relative"))
    assert find(root, "path").get("d") == "m 0 0 l 10 10 l 10 -10"


def test_visibility_removes_hidden_elements_and_unused_definitions():
    root = load_svg(
        f"""<svg xmlns="{SVG}">
            <defs>
              <linearGradient id="used"><stop offset="0"/></linearGradient>
              <linearGradient id="unused"><stop offset="1"/></linearGradient>
            </defs>
            <rect width="10" height="10" fill="url(#used)"/>
            <rect width="5" height="5" display="none"/>
            <path d="M0 0 L1 1" style="visibility: hidden"/>
            <circle cx="1" cy="1" r="1" opacity="0"/>
        </svg>"""
    )
    removed = visibility.cleanup_visibility(root, context())
    assert removed == 4  # hidden rect, hidden path, invisible circle, unused gradient
    assert len([element for element in root.iter() if localname(element) == "rect"]) == 1
    assert find(root, "linearGradient", id="used") is not None
    assert not [element for element in root.iter() if localname(element) == "circle"]


def test_visibility_hidden_group_with_a_visible_child_is_kept():
    root = load_svg(
        f'<svg xmlns="{SVG}"><g visibility="hidden">'
        '<text visibility="visible">still here</text></g></svg>'
    )
    visibility.cleanup_visibility(root, context())
    assert find(root, "g") is not None
    assert find(root, "text").text == "still here"


def test_hidden_elements_with_ids_are_kept():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect id="target" width="1" height="1" display="none"/></svg>'
    )
    visibility.cleanup_visibility(root, context())
    assert find(root, "rect", id="target") is not None


def test_inline_style_overrides_visibility_attribute():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect display="none" style="display: inline" '
        'width="1" height="1"/></svg>'
    )
    visibility.cleanup_visibility(root, context())
    assert find(root, "rect") is not None


def test_definition_container_is_kept_for_referenced_descendant():
    root = load_svg(
        f'<svg xmlns="{SVG}"><defs><g id="container">'
        '<path id="shape" d="M0 0L1 1"/></g></defs><use href="#shape"/></svg>'
    )
    visibility.cleanup_visibility(root, context())
    assert find(root, "g", id="container") is not None
    assert find(root, "path", id="shape") is not None


def test_definition_referenced_by_retained_css_is_kept():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>:root {{ --paint: url(#paint) }} '
        "rect { fill: var(--paint) }</style><defs>"
        '<linearGradient id="paint"><stop offset="0"/></linearGradient>'
        '</defs><rect width="1" height="1"/></svg>'
    )
    visibility.cleanup_visibility(root, context())
    assert find(root, "linearGradient", id="paint") is not None


def test_invalid_transform_is_preserved_with_warning():
    root = load_svg(
        f'<svg xmlns="{SVG}"><path transform="translate(foo)" d="M0 0L1 1"/></svg>'
    )
    ctx = context()
    geometry.simplify_geometry(root, ctx)
    assert find(root, "path").get("transform") == "translate(foo)"
    assert ctx.warnings


def test_pipeline_is_idempotent_after_id_cleanup():
    svg = (
        f'<svg xmlns="{SVG}"><g id="wrapper"><g><path id="shape" '
        'd="M 10.0004 10.0004 L 20 20"/></g></g></svg>'
    )
    first = clean(svg, CleanConfig()).svg
    second = clean(first, CleanConfig()).svg
    assert second == first


def test_script_prevents_id_cleanup():
    root = load_svg(
        f'<svg xmlns="{SVG}"><rect id="longIdentifier" width="1" height="1"/>'
        "<script>document.getElementById('longIdentifier')</script></svg>"
    )
    ctx = context()
    ids.cleanup_ids(root, ctx)
    assert find(root, "rect").get("id") == "longIdentifier"
    assert ctx.warnings


def test_hidden_element_keeps_its_id_for_idempotence():
    svg = (
        f'<svg xmlns="{SVG}"><rect id="hidden" display="none" '
        'width="1" height="1"/></svg>'
    )
    first = clean(svg, CleanConfig()).svg
    second = clean(first, CleanConfig()).svg
    assert first == second
    assert 'id="hidden"' in first
