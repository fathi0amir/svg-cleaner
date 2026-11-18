"""Identifier cleanup: dedupe, shorten, drop unused ids and rewrite references."""

from __future__ import annotations

import re
import string
from collections.abc import Iterator

from lxml import etree

from ..document import collect, has_external_stylesheet, iter_elements
from .context import PassContext

URL_REFERENCE = re.compile(r"url\(\s*['\"]?#([^'\")\s]+)['\"]?\s*\)")
#: SMIL timing references such as ``begin="node.click"`` or ``end="a.repeat(2)"``.
SMIL_REFERENCE = re.compile(r"([A-Za-z_][\w-]*)\.")
TOKEN_LIST_ATTRIBUTES = {
    "aria-labelledby",
    "aria-describedby",
    "aria-controls",
    "aria-owns",
    "aria-flowto",
}
PROTECTED_PREFIXES = ("mermaid",)

_ALPHABET = string.ascii_lowercase + string.ascii_uppercase


def short_name(index: int) -> str:
    """``0 -> a``, ``51 -> Z``, ``52 -> aa``."""
    index += 1
    out = ""
    while index:
        index, remainder = divmod(index - 1, len(_ALPHABET))
        out = _ALPHABET[remainder] + out
    return out


def _attribute_name(key: str) -> str:
    return key.split("}", 1)[-1] if "}" in key else key


def referenced_ids(root: etree._Element) -> set[str]:
    """Every id reached from ``url(#id)``, ``href="#id"`` or ``aria-*``."""
    references: set[str] = set()
    for style in collect(root, ["style"]):
        for match in URL_REFERENCE.finditer("".join(style.itertext())):
            references.add(match.group(1))
    for element in iter_elements(root):
        for key, value in element.attrib.items():
            for match in URL_REFERENCE.finditer(value):
                references.add(match.group(1))
            name = _attribute_name(key)
            if name == "href" and value.strip().startswith("#"):
                references.add(value.strip()[1:])
            elif name in TOKEN_LIST_ATTRIBUTES:
                references.update(token for token in value.split() if token)
            elif name in ("begin", "end"):
                references.update(SMIL_REFERENCE.findall(value))
    return references


def _ids(root: etree._Element) -> Iterator[tuple[etree._Element, str]]:
    for element in iter_elements(root):
        identifier = element.get("id")
        if identifier:
            yield element, identifier


def _unique(candidate: str, taken: set[str]) -> str:
    name = candidate
    counter = 2
    while name in taken:
        name = f"{candidate}-{counter}"
        counter += 1
    taken.add(name)
    return name


def dedupe(root: etree._Element, ctx: PassContext) -> int:
    """Give every duplicate id a fresh name (the first occurrence wins)."""
    seen: dict[str, etree._Element] = {}
    renamed = 0
    taken = {identifier for _element, identifier in _ids(root)}
    for element, identifier in list(_ids(root)):
        first = seen.get(identifier)
        if first is None:
            seen[identifier] = element
            continue
        new_id = _unique(f"{identifier}-2", taken)
        element.set("id", new_id)
        renamed += 1
        ctx.warn("ids", f"duplicate id #{identifier} renamed to #{new_id}")
    return renamed


def cleanup_ids(root: etree._Element, ctx: PassContext) -> int:
    """Shorten referenced ids and drop ids nothing points at."""
    config = ctx.config
    if collect(root, ["style"]):
        ctx.warn("ids", "<style> block present, leaving ids alone so CSS keeps matching")
        return 0
    if has_external_stylesheet(root):
        ctx.warn("ids", "external stylesheet present, leaving ids alone")
        return 0
    if collect(root, ["script"]) or any(
        any(name.split("}", 1)[-1].lower().startswith("on") for name in element.attrib)
        for element in iter_elements(root)
    ):
        ctx.warn("ids", "script/event handler present, leaving ids alone")
        return 0

    dedupe(root, ctx)
    references = referenced_ids(root)
    identifiers = list(_ids(root))
    taken = {identifier for _element, identifier in identifiers}
    mapping: dict[str, str] = {}
    changed = 0

    for element, identifier in identifiers:
        protected = element.getparent() is None or identifier.startswith(PROTECTED_PREFIXES)
        if identifier in references:
            if not config.shorten_ids:
                continue
            candidate = short_name(len(mapping))
            if len(candidate) >= len(identifier):
                continue
            mapping[identifier] = _unique(candidate, taken - {identifier})
            changed += 1
        elif protected or _is_invisible(element):
            continue
        elif config.remove_unused_ids:
            del element.attrib["id"]
            changed += 1

    if not mapping:
        return changed

    for element in iter_elements(root):
        for key, value in list(element.attrib.items()):
            updated = _rewrite(value, key, mapping)
            if updated != value:
                element.set(key, updated)

    for element, identifier in identifiers:
        if identifier in mapping and element.get("id") == identifier:
            element.set("id", mapping[identifier])
    return changed


def _is_invisible(element: etree._Element) -> bool:
    from ..document import local_style

    properties = local_style(element)
    if (properties.get("display") or "").strip().lower() == "none":
        return True
    if (properties.get("visibility") or "").strip().lower() in ("hidden", "collapse"):
        return True
    try:
        return float(properties.get("opacity", "1")) == 0
    except ValueError:
        return False


def _rewrite(value: str, key: str, mapping: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        new = mapping.get(match.group(1))
        return f"url(#{new})" if new else match.group(0)

    updated = URL_REFERENCE.sub(replace, value)
    name = _attribute_name(key)
    stripped = updated.strip()
    if name == "href" and stripped.startswith("#"):
        new = mapping.get(stripped[1:])
        if new:
            return f"#{new}"
    if name in TOKEN_LIST_ATTRIBUTES:
        tokens = [mapping.get(token, token) for token in updated.split()]
        return " ".join(tokens)
    if name in ("begin", "end"):
        return SMIL_REFERENCE.sub(
            lambda match: f"{mapping.get(match.group(1), match.group(1))}.", updated
        )
    return updated
