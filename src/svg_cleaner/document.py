"""Reading, writing and inspecting SVG documents with lxml."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from lxml import etree

from .errors import ParseError

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NSMAP = {"svg": SVG_NS, "xlink": XLINK_NS}

#: Elements whose character data is content, never indentation.
TEXTUAL_TAGS = {
    "text",
    "tspan",
    "textPath",
    "style",
    "script",
    "title",
    "desc",
    "foreignObject",
}

_LENGTH_RE = re.compile(r"^\s*([-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*([a-z%]*)\s*$")
_UNIT_SCALE = {"": 1.0, "px": 1.0, "pt": 4.0 / 3.0, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54,
               "mm": 96 / 25.4}


def qname(local: str) -> str:
    """Fully qualified SVG element name for lxml."""
    return f"{{{SVG_NS}}}{local}"


def localname(element: etree._Element) -> str | None:
    """Local tag name, or ``None`` for comments / processing instructions."""
    if not isinstance(element.tag, str):
        return None
    return etree.QName(element).localname


def namespace(element: etree._Element) -> str | None:
    if not isinstance(element.tag, str):
        return None
    return etree.QName(element).namespace


def is_svg_element(element: etree._Element, *names: str) -> bool:
    name = localname(element)
    return name is not None and name in names


def iter_elements(root: etree._Element) -> Iterator[etree._Element]:
    """Every real element below (and including) *root*, in document order."""
    for element in root.iter():
        if isinstance(element.tag, str):
            yield element


def parent_map(root: etree._Element) -> dict[etree._Element, etree._Element]:
    return {child: parent for parent in iter_elements(root) for child in parent}


# -- styles -------------------------------------------------------------------


def parse_style(value: str | None) -> dict[str, str]:
    """Parse a CSS ``style`` attribute into a property dict."""
    props: dict[str, str] = {}
    for part in (value or "").split(";"):
        key, sep, val = part.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key:
            props[key] = val.strip()
    return props


def format_style(props: dict[str, str]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in props.items())


def get_style(element: etree._Element) -> dict[str, str]:
    return parse_style(element.get("style"))


def set_style(element: etree._Element, **props: object) -> None:
    """Merge properties into the ``style`` attribute (``_`` becomes ``-``)."""
    style = get_style(element)
    for key, value in props.items():
        if value is None:
            style.pop(key.replace("_", "-"), None)
        else:
            style[key.replace("_", "-")] = str(value)
    if style:
        element.set("style", format_style(style))
    elif "style" in element.attrib:
        del element.attrib["style"]


def unset_style(element: etree._Element, *names: str) -> None:
    style = get_style(element)
    changed = False
    for name in names:
        if style.pop(name.lower(), None) is not None:
            changed = True
    if not changed:
        return
    if style:
        element.set("style", format_style(style))
    elif "style" in element.attrib:
        del element.attrib["style"]


def local_style(element: etree._Element) -> dict[str, str]:
    """Inline style plus presentation attributes as one property view."""
    props = {name: value for name, value in element.attrib.items() if ":" not in name}
    props.update(get_style(element))
    return props


def inherited(element: etree._Element, prop: str, default: str | None = None) -> str | None:
    """Resolve an inherited presentation property up the ancestor chain."""
    node: etree._Element | None = element
    while node is not None:
        props = local_style(node)
        if prop in props:
            return props[prop]
        if node.get(prop) is not None:
            return node.get(prop)
        node = node.getparent()
    return default


def parse_number(value: str | None, default: float | None = None) -> float | None:
    """Parse a plain number, tolerating whitespace; ``None`` when unparseable."""
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def parse_length(value: str | None, default: float | None = None) -> float | None:
    """Parse a CSS/SVG length into user units; percentages and ``em`` yield None."""
    if value is None:
        return default
    match = _LENGTH_RE.match(str(value))
    if not match:
        return default
    number = float(match.group(1))
    unit = match.group(2).lower()
    scale = _UNIT_SCALE.get(unit)
    if scale is None:
        return default
    return number * scale


def fmt(value: float, precision: int = 3) -> str:
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# -- parsing / serialization --------------------------------------------------


def load_svg(source: str | bytes) -> etree._Element:
    """Parse an SVG document, raising :class:`ParseError` on bad input."""
    data = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_comments=False,
        remove_pis=False,
        huge_tree=False,
        recover=False,
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"not well-formed XML: {exc}") from exc
    if localname(root) != "svg":
        raise ParseError(f"root element is <{localname(root)}>, expected <svg>")
    return root


def strip_whitespace(root: etree._Element) -> None:
    """Drop indentation whitespace, leaving textual subtrees untouched."""

    def walk(element: etree._Element) -> None:
        if localname(element) in TEXTUAL_TAGS:
            return
        if element.text is not None and not element.text.strip():
            element.text = None
        for child in element:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail is not None and not child.tail.strip():
                child.tail = None

    walk(root)


def indent_tree(root: etree._Element, indent: int = 2) -> None:
    """Re-indent the tree, skipping subtrees whose text is content."""
    strip_whitespace(root)
    pad_unit = " " * indent

    def walk(element: etree._Element, depth: int) -> None:
        if localname(element) in TEXTUAL_TAGS:
            return
        children = [child for child in element if isinstance(child.tag, str)]
        if not children:
            return
        inner = "\n" + pad_unit * (depth + 1)
        element.text = inner
        for index, child in enumerate(children):
            walk(child, depth + 1)
            child.tail = inner if index < len(children) - 1 else "\n" + pad_unit * depth

    walk(root, 0)
    root.tail = None


def dump_svg(
    root: etree._Element,
    *,
    pretty: bool = True,
    indent: int = 2,
    xml_declaration: bool = True,
) -> bytes:
    """Serialize to UTF-8 bytes."""
    if pretty:
        indent_tree(root, indent)
    else:
        strip_whitespace(root)
    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=xml_declaration,
        pretty_print=False,
    )


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file in the same directory)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise



def collect(root: etree._Element, tags: Iterable[str]) -> list[etree._Element]:
    wanted = set(tags)
    return [element for element in iter_elements(root) if localname(element) in wanted]
