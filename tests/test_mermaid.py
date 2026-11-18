"""Tests for the mermaid-cli repair pass."""

from __future__ import annotations

from svg_cleaner.config import CleanConfig
from svg_cleaner.document import load_svg, localname
from svg_cleaner.passes import PassContext, mermaid, styles

SVG = "http://www.w3.org/2000/svg"

FLOWCHART = f"""<svg xmlns="{SVG}" id="mermaid-1" style="font-size: 16px">
  <style>
    .label {{ fill: #333333; font-size: 16px }}
    .cluster rect {{ fill: #ffffde; stroke: #aaaa33 }}
    .node rect {{ fill: #ececff; stroke: #9370db }}
    @keyframes dash {{ from {{ stroke-dashoffset: 1 }} }}
  </style>
  <g class="cluster" id="group">
    <rect x="0" y="0" width="200" height="100"/>
    <g class="cluster-label"><text><tspan x="0" dy="1.2em">Group</tspan></text></g>
    <g class="node" transform="translate(60,60)">
      <rect x="-20" y="-10" width="40" height="20"/>
      <g class="label" transform="translate(-12,-8)">
        <text><tspan x="0" dy="1em">A</tspan></text>
      </g>
    </g>
  </g>
</svg>"""

FOREIGN = f"""<svg xmlns="{SVG}" id="mermaid-2" style="font-size: 16px">
  <g class="node" transform="translate(50,50)">
    <g class="label">
      <foreignObject x="0" y="0" width="60" height="30">
        <div xmlns="http://www.w3.org/1999/xhtml"><p>Hello</p><p>World</p></div>
      </foreignObject>
    </g>
  </g>
</svg>"""


def context(**kwargs) -> PassContext:
    return PassContext(config=CleanConfig(**kwargs), profile="mermaid")


def texts(root):
    return [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and localname(element) == "text"
    ]


def first_row(text):
    return next(child for child in text if localname(child) == "tspan")


def test_css_is_inlined_before_labels_are_placed():
    root = load_svg(FLOWCHART)
    ctx = context()
    assert styles.inline_css(root, ctx) == 3
    assert not [element for element in root.iter() if localname(element) == "style"]
    node_rect = next(
        element
        for element in root.iter()
        if isinstance(element.tag, str)
        and localname(element) == "rect"
        and "#ececff" in (element.get("style") or "")
    )
    assert node_rect.getparent().get("class") == "node"


def test_labels_are_centered_on_their_node_and_cluster_title_moves_to_the_top():
    root = load_svg(FLOWCHART)
    ctx = context()
    styles.inline_css(root, ctx)
    count = mermaid.repair_mermaid(root, ctx)
    assert count == 2

    cluster_text, node_text = texts(root)
    node_row = first_row(node_text)
    # node center is (0,0) in node space: label group sits at (-12,-8), so the
    # text needs x=12 and baseline 8 + 0.35*16 = 13.6
    assert (node_row.get("x"), node_row.get("y")) == ("12.00", "13.60")
    assert node_text.get("text-anchor") == "middle"
    assert node_row.get("dy") is None
    # subgraph title: centred on the 200px-wide rect, 6px below the top edge
    cluster_row = first_row(cluster_text)
    assert (cluster_row.get("x"), cluster_row.get("y")) == ("100.00", "19.60")


def test_node_labels_inside_a_cluster_are_not_repositioned_as_titles():
    root = load_svg(FLOWCHART)
    ctx = context()
    styles.inline_css(root, ctx)
    mermaid.repair_mermaid(root, ctx)
    node_row = first_row(texts(root)[-1])
    # a cluster-title style placement would put it at x=100
    assert node_row.get("x") == "12.00"


def test_foreign_objects_become_editable_text():
    root = load_svg(FOREIGN)
    ctx = context()
    mermaid.repair_mermaid(root, ctx)
    assert not [element for element in root.iter() if localname(element) == "foreignObject"]
    text = texts(root)[0]
    rows = [child for child in text if localname(child) == "tspan"]
    assert [row.text for row in rows] == ["Hello", "World"]
    assert rows[0].get("x") == rows[1].get("x") == "0.00"


def test_css_scoped_to_the_root_id_is_inlined():
    # mermaid scopes every rule to the root id, so the mirror must carry it
    root = load_svg(
        f'<svg xmlns="{SVG}" id="my-svg"><style>#my-svg rect {{ fill: red }}</style>'
        '<g class="node"><rect width="1" height="1"/></g></svg>'
    )
    ctx = context()
    assert styles.inline_css(root, ctx) == 1
    rect = next(
        element
        for element in root.iter()
        if isinstance(element.tag, str) and localname(element) == "rect"
    )
    assert rect.get("style") == "fill: red"


def test_detect_font_size_reads_inlined_css():
    root = load_svg(
        f'<svg xmlns="{SVG}"><style>.node text {{ font-size: 20px }}</style>'
        '<g class="node"><text><tspan>A</tspan></text></g></svg>'
    )
    styles.inline_css(root, context())
    assert mermaid.detect_font_size(root) == 20.0


def test_foreign_objects_can_be_disabled():
    root = load_svg(FOREIGN)
    ctx = context(convert_foreign_objects=False)
    mermaid.repair_mermaid(root, ctx)
    assert [element for element in root.iter() if localname(element) == "foreignObject"]
