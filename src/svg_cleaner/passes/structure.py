"""Structural cleanup: comments, metadata, editor junk and useless groups."""

from __future__ import annotations

from lxml import etree

from ..document import collect, iter_elements, localname, namespace
from .context import PassContext

#: Namespaces written by editors and never needed for rendering.
EDITOR_NAMESPACES = {
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://www.inkscape.org/namespaces/inkscape",
    "http://ns.adobe.com/AdobeIllustrator/10.0/",
    "http://ns.adobe.com/AdobeSVGViewerExtensions/3.0/",
    "http://www.bohemiancoding.com/sketch/ns",
    "http://www.serif.com/",
    "http://creativecommons.org/ns#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://purl.org/dc/elements/1.1/",
}


def _remove(element: etree._Element) -> bool:
    parent = element.getparent()
    if parent is None:
        return False
    parent.remove(element)
    return True


def cleanup_document(root: etree._Element, ctx: PassContext) -> int:
    """Drop comments, <metadata>, editor elements and editor namespaced attrs."""
    config = ctx.config
    removed = 0

    if config.remove_comments:
        for element in list(root.iter()):
            if isinstance(element.tag, str):
                continue
            if isinstance(element, etree._Comment):
                removed += _remove(element)

    if config.remove_metadata:
        for element in collect(root, ["metadata"]):
            removed += _remove(element)

    if config.remove_editor_data:
        for element in list(iter_elements(root)):
            if namespace(element) in EDITOR_NAMESPACES:
                removed += _remove(element)
        for element in iter_elements(root):
            for key in list(element.attrib):
                if "}" in key and key.split("}", 1)[0][1:] in EDITOR_NAMESPACES:
                    del element.attrib[key]
                    removed += 1

    # empty containers that cannot matter
    for element in list(iter_elements(root)):
        if len(element) or element.getparent() is None or element.get("id"):
            continue
        name = localname(element)
        if name == "defs" and not element.attrib:
            removed += _remove(element)
        elif name == "g" and not element.attrib:
            removed += _remove(element)

    etree.cleanup_namespaces(root)
    return removed


def collapse_groups(root: etree._Element, ctx: PassContext) -> int:
    """Replace attribute-less single-child <g> wrappers by their child."""
    collapsed = 0
    changed = True
    while changed:
        changed = False
        for group in list(collect(root, ["g"])):
            parent = group.getparent()
            if parent is None or len(group) != 1 or group.attrib:
                continue
            child = group[0]
            if not isinstance(child.tag, str):
                continue
            index = parent.index(group)
            parent.remove(group)
            parent.insert(index, child)
            collapsed += 1
            changed = True
    return collapsed
