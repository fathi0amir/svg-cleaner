from __future__ import annotations

import pytest

from svg_cleaner.document import (
    atomic_write,
    dump_svg,
    get_style,
    indent_tree,
    inherited,
    load_svg,
    localname,
    parse_length,
    parse_number,
    set_style,
    unset_style,
)
from svg_cleaner.errors import ParseError

MINIMAL = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


def test_load_and_roundtrip():
    root = load_svg(MINIMAL)
    assert localname(root) == "svg"
    output = dump_svg(root, pretty=True)
    assert output.startswith(b"<?xml")
    assert b"<rect" in output
    assert b'xmlns="http://www.w3.org/2000/svg"' in output


def test_load_rejects_non_svg():
    with pytest.raises(ParseError):
        load_svg("<html/>")
    with pytest.raises(ParseError):
        load_svg("<svg>")


def test_style_helpers_merge_and_remove():
    root = load_svg(MINIMAL)
    rect = root[0]
    set_style(rect, fill="#fff", stroke_width="2")
    assert get_style(rect) == {"fill": "#fff", "stroke-width": "2"}
    set_style(rect, fill=None)
    assert get_style(rect) == {"stroke-width": "2"}
    unset_style(rect, "stroke-width")
    assert rect.get("style") is None


def test_inherited_presentation_property():
    root = load_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><g fill="none">'
        '<path d="M0 0 L1 1" stroke="red"/></g></svg>'
    )
    path = root[0][0]
    assert inherited(path, "fill") == "none"
    assert inherited(path, "stroke") == "red"
    assert inherited(path, "stroke-width") is None


def test_length_and_number_parsing():
    assert parse_length("10") == 10.0
    assert parse_length("10px") == 10.0
    assert parse_length("72pt") == pytest.approx(96.0)
    assert parse_length("50%") is None
    assert parse_length("garbage") is None
    assert parse_number(" 2.5 ") == 2.5
    assert parse_number(None, 7.0) == 7.0


def test_pretty_and_minified_output():
    root = load_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><g><rect width="1" height="1"/></g></svg>'
    )
    pretty = dump_svg(root, pretty=True, indent=2).decode()
    assert "\n  <g>" in pretty
    assert "\n    <rect" in pretty

    root = load_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><g><rect width="1" height="1"/></g></svg>'
    )
    compact = dump_svg(root, pretty=False).decode()
    body = compact.split("?>", 1)[1].strip()
    assert "\n" not in body
    assert body.startswith("<svg")


def test_text_content_is_not_reindented():
    root = load_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><text><tspan>a</tspan><tspan>b</tspan></text></svg>'
    )
    indent_tree(root)
    text = root[0]
    assert text.text is None
    assert text[0].text == "a"
    assert text[1].text == "b"


def test_atomic_write(tmp_path):
    target = tmp_path / "nested" / "out.svg"
    atomic_write(target, b"<svg/>")
    assert target.read_bytes() == b"<svg/>"
    assert [p.name for p in target.parent.iterdir()] == ["out.svg"]
