"""Removal of invisible elements and unreferenced definitions."""

from __future__ import annotations

from lxml import etree

from ..document import get_style, iter_elements, localname
from .context import PassContext
from .ids import referenced_ids

#: Subtrees that define reusable content and must not be pruned as "hidden".
DEFINITION_CONTAINERS = {
    "defs",
    "clipPath",
    "mask",
    "symbol",
    "marker",
    "pattern",
    "linearGradient",
    "radialGradient",
    "filter",
}
DEFINITION_TAGS = {
    "clipPath",
    "mask",
    "marker",
    "pattern",
    "linearGradient",
    "radialGradient",
    "filter",
}


def _inside_definitions(element: etree._Element) -> bool:
    node = element.getparent()
    while node is not None:
        if localname(node) in DEFINITION_CONTAINERS:
            return True
        node = node.getparent()
    return False


def _is_hidden(element: etree._Element) -> bool:
    display = element.get("display") or get_style(element).get("display")
    if display and display.strip().lower() == "none":
        return True
    visibility = element.get("visibility") or get_style(element).get("visibility")
    if visibility and visibility.strip().lower() in ("hidden", "collapse"):
        return True
    opacity = element.get("opacity") or get_style(element).get("opacity")
    if opacity is not None:
        try:
            if float(opacity) == 0:
                return True
        except ValueError:
            pass
    return False


def remove_hidden(root: etree._Element, ctx: PassContext) -> int:
    """Delete elements that cannot paint anything."""
    removed = 0
    for element in list(iter_elements(root)):
        if element.getparent() is None or element.get("id"):
            continue
        if _inside_definitions(element) or not _is_hidden(element):
            continue
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
            removed += 1
    return removed


def prune_definitions(root: etree._Element, ctx: PassContext) -> int:
    """Delete unreferenced gradients, clipPaths, masks, markers and defs."""
    references = referenced_ids(root)
    removed = 0

    for element in list(iter_elements(root)):
        name = localname(element)
        identifier = element.get("id")
        if name in DEFINITION_TAGS and identifier and identifier not in references:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
        elif name == "defs":
            for child in list(element):
                if not isinstance(child.tag, str):
                    element.remove(child)
                    removed += 1
                    continue
                child_id = child.get("id")
                if child_id and child_id not in references:
                    element.remove(child)
                    removed += 1
            if len(element) == 0 and not element.attrib and element.getparent() is not None:
                element.getparent().remove(element)
                removed += 1
    return removed


def cleanup_visibility(root: etree._Element, ctx: PassContext) -> int:
    config = ctx.config
    removed = 0
    if config.remove_hidden:
        removed += remove_hidden(root, ctx)
    removed += prune_definitions(root, ctx)
    return removed
