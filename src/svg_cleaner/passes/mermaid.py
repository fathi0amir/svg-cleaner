"""Repair mermaid-cli exports so Inkscape lays them out like a browser."""

from __future__ import annotations

import re

from lxml import etree

from ..document import collect, get_style, iter_elements, localname, qname, set_style
from .context import PassContext

TRANSLATE_RE = re.compile(r"translate\(\s*([-+\d.eE]+)(?:[\s,]+([-+\d.eE]+))?\s*\)")
FONT_SIZE_RE = re.compile(r"font-size:\s*([\d.]+)px")
HTML_BLOCK_TAGS = {"p", "div", "li"}


def translate_of(element: etree._Element) -> tuple[float, float]:
    match = TRANSLATE_RE.search(element.get("transform") or "")
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2) or 0.0)


def has_class(element: etree._Element, name: str) -> bool:
    return name in (element.get("class") or "").split()


def text_rows(text: etree._Element) -> list[etree._Element]:
    return [child for child in text if localname(child) == "tspan"]


def detect_font_size(root: etree._Element) -> float:
    """Read the label font size from inline styles, attributes or leftover CSS."""
    for element in iter_elements(root):
        match = FONT_SIZE_RE.search(element.get("style") or "")
        if match:
            return float(match.group(1))
        value = (element.get("font-size") or "").strip()
        if value.endswith("px"):
            try:
                return float(value[:-2])
            except ValueError:
                continue
    css = "".join("".join(style.itertext()) for style in collect(root, ["style"]))
    match = FONT_SIZE_RE.search(css)
    return float(match.group(1)) if match else 16.0


def detect_fill(root: etree._Element) -> str:
    fill = get_style(root).get("fill") or root.get("fill")
    if fill:
        return fill
    for element in collect(root, ["text", "tspan"]):
        value = element.get("fill") or get_style(element).get("fill")
        if value:
            return value
    css = "".join("".join(style.itertext()) for style in collect(root, ["style"]))
    match = re.search(r"#[\w-]+\s*\{[^}]*?[;{]fill:\s*(#[0-9A-Fa-f]{3,8})", css)
    return match.group(1) if match else "#333333"


def place_text(
    text: etree._Element,
    rows: list[etree._Element],
    cx: float,
    first_y: float,
    line_px: float,
    size: float,
    anchor: str,
) -> None:
    """Write explicit pixel coordinates for every row (no em offsets)."""
    text.attrib.pop("x", None)
    text.attrib.pop("y", None)
    text.set("text-anchor", anchor)
    text.set("font-size", f"{size:g}px")
    set_style(text, text_anchor=anchor, font_size=f"{size:g}px")
    if rows:
        for index, row in enumerate(rows):
            row.set("x", f"{cx:.2f}")
            row.set("y", f"{first_y + index * line_px:.2f}")
            row.attrib.pop("dy", None)
    else:
        text.set("x", f"{cx:.2f}")
        text.set("y", f"{first_y:.2f}")


def html_lines(foreign_object: etree._Element) -> list[str]:
    lines = [""]

    def walk(element: etree._Element) -> None:
        if element.text:
            lines[-1] += element.text
        for child in element:
            name = localname(child)
            if name is None:
                if child.tail:
                    lines[-1] += child.tail
                continue
            if name == "br":
                lines.append("")
            elif name in HTML_BLOCK_TAGS and lines[-1].strip():
                lines.append("")
            walk(child)
            if name in HTML_BLOCK_TAGS:
                lines.append("")
            if child.tail:
                lines[-1] += child.tail

    walk(foreign_object)
    return [" ".join(line.split()) for line in lines if line.strip()]


def convert_foreign_objects(root: etree._Element, ctx: PassContext, size: float) -> int:
    config = ctx.config
    fill = config.fo_fill or detect_fill(root)
    count = 0
    for foreign_object in list(collect(root, ["foreignObject"])):
        parent = foreign_object.getparent()
        if parent is None:
            continue
        index = parent.index(foreign_object)
        lines = html_lines(foreign_object)
        width = float(foreign_object.get("width") or 0)
        height = float(foreign_object.get("height") or 0)
        x = float(foreign_object.get("x") or 0)
        y = float(foreign_object.get("y") or 0)
        parent.remove(foreign_object)
        if not lines:
            continue

        text = etree.Element(qname("text"), {"fill": fill})
        if config.fo_font_family:
            text.set("font-family", config.fo_font_family)
        for line in lines:
            row = etree.SubElement(text, qname("tspan"))
            row.text = line
        rows = text_rows(text)
        line_px = config.line_height * size
        first = y + height / 2 - (len(lines) - 1) * line_px / 2 + config.baseline * size
        place_text(text, rows, x + width / 2, first, line_px, size, "middle")
        parent.insert(index, text)
        count += 1
    return count


def center_node_labels(root: etree._Element, ctx: PassContext, size: float) -> int:
    config = ctx.config
    line_px = config.line_height * size
    count = 0
    for text in collect(root, ["text"]):
        # climb to the node / edgeLabel group, summing translates below it:
        # that group's origin is the label's center
        sx = sy = 0.0
        element = text.getparent()
        found = False
        while element is not None:
            if has_class(element, "node") or has_class(element, "edgeLabel"):
                found = True
                break
            tx, ty = translate_of(element)
            sx += tx
            sy += ty
            element = element.getparent()
        if not found:
            continue

        rows = text_rows(text)
        number = max(len(rows), 1)
        cx = -sx + config.label_dx
        first = (
            -sy - (number - 1) * line_px / 2 + config.baseline * size + config.label_dy
        )
        place_text(text, rows, cx, first, line_px, size, "middle")
        count += 1
    return count


def place_cluster_labels(root: etree._Element, ctx: PassContext, size: float) -> int:
    config = ctx.config
    line_px = config.line_height * size
    anchor = {"left": "start", "center": "middle", "right": "end"}[config.cluster_align]
    count = 0

    for cluster in collect(root, ["g"]):
        if not has_class(cluster, "cluster"):
            continue
        rect = next((child for child in cluster if localname(child) == "rect"), None)
        if rect is None:
            continue
        rx = float(rect.get("x", 0))
        ry = float(rect.get("y", 0))
        width = float(rect.get("width", 0))

        if config.cluster_align == "left":
            box_x = rx + config.cluster_pad_x
        elif config.cluster_align == "right":
            box_x = rx + width - config.cluster_pad_x
        else:
            box_x = rx + width / 2

        for text in collect(cluster, ["text"]):
            # node labels nested inside the cluster are handled by step 3
            ancestor = text.getparent()
            nested = False
            sx = sy = 0.0
            while ancestor is not None and ancestor is not cluster:
                if has_class(ancestor, "node") or has_class(ancestor, "edgeLabel"):
                    nested = True
                    break
                tx, ty = translate_of(ancestor)
                sx += tx
                sy += ty
                ancestor = ancestor.getparent()
            if nested:
                continue

            rows = text_rows(text)
            first = ry + config.cluster_pad_top + size / 2 + config.baseline * size - sy
            place_text(text, rows, box_x - sx, first, line_px, size, anchor)
            count += 1
    return count


def repair_mermaid(root: etree._Element, ctx: PassContext) -> int:
    """Run the mermaid-cli repair steps selected by the configuration."""
    config = ctx.config
    size = config.font_size or detect_font_size(root)
    cluster_size = config.cluster_font_size or size
    count = 0
    if config.convert_foreign_objects:
        count += convert_foreign_objects(root, ctx, size)
    if config.fix_mermaid_labels:
        count += center_node_labels(root, ctx, size)
        count += place_cluster_labels(root, ctx, cluster_size)
    return count


__all__ = ["repair_mermaid", "detect_font_size", "detect_fill", "html_lines"]
