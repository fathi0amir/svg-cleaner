"""Removal of invisible elements and unreferenced definitions."""

from __future__ import annotations

from lxml import etree

from ..document import has_external_stylesheet, iter_elements, local_style, localname
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


ANIMATION_TAGS = {"animate", "animateTransform", "animateMotion", "set"}


def _has_animation(element: etree._Element) -> bool:
    return any(
        isinstance(child.tag, str) and localname(child) in ANIMATION_TAGS
        for child in element.iterdescendants()
    )


def _hidden_only_by_visibility(element: etree._Element) -> bool:
    properties = local_style(element)
    display = properties.get("display")
    if display and display.strip().lower() == "none":
        return False
    opacity = properties.get("opacity")
    try:
        if opacity is not None and float(opacity) == 0:
            return False
    except ValueError:
        pass
    return True


def _visibility_reenabled(element: etree._Element) -> bool:
    """A descendant may override an inherited ``visibility: hidden``."""
    for descendant in element.iterdescendants():
        if not isinstance(descendant.tag, str):
            continue
        value = local_style(descendant).get("visibility")
        if value and value.strip().lower() == "visible":
            return True
    return False


def _is_hidden(element: etree._Element) -> bool:
    properties = local_style(element)
    display = properties.get("display")
    if display and display.strip().lower() == "none":
        return True
    visibility = properties.get("visibility")
    if visibility and visibility.strip().lower() in ("hidden", "collapse"):
        return True
    opacity = properties.get("opacity")
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
        if _has_animation(element):
            continue
        if _hidden_only_by_visibility(element) and _visibility_reenabled(element):
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

    def subtree_referenced(element: etree._Element) -> bool:
        return bool(
            any(
                node.get("id") in references
                for node in element.iter()
                if isinstance(node.tag, str)
            )
        )

    for element in list(iter_elements(root)):
        name = localname(element)
        identifier = element.get("id")
        if (
            name in DEFINITION_TAGS
            and identifier
            and identifier not in references
            and not subtree_referenced(element)
        ):
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
                if child_id and child_id not in references and not subtree_referenced(child):
                    element.remove(child)
                    removed += 1
            if len(element) == 0 and not element.attrib and element.getparent() is not None:
                element.getparent().remove(element)
                removed += 1
    return removed


def cleanup_visibility(root: etree._Element, ctx: PassContext) -> int:
    if has_external_stylesheet(root):
        ctx.warn("visibility", "external stylesheet present, leaving visibility and defs alone")
        return 0
    config = ctx.config
    removed = 0
    if config.remove_hidden:
        removed += remove_hidden(root, ctx)
    removed += prune_definitions(root, ctx)
    return removed
