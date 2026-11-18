"""CSS inlining and style-to-attribute normalization."""

from __future__ import annotations

import cssselect
import tinycss2
from lxml import etree

from ..document import collect, format_style, get_style, iter_elements
from .context import PassContext

#: Properties meaningless in a static drawing.
SKIP_PROPERTIES = {"animation", "cursor", "pointer-events"}
#: Values Inkscape and other renderers do not understand.
SKIP_VALUES = {"revert", "revert-layer"}

#: Presentation attributes that may live in an attribute instead of style.
PRESENTATION_ATTRIBUTES = {
    "alignment-baseline",
    "baseline-shift",
    "clip",
    "clip-path",
    "clip-rule",
    "color",
    "color-interpolation",
    "color-interpolation-filters",
    "color-rendering",
    "direction",
    "display",
    "dominant-baseline",
    "fill",
    "fill-opacity",
    "fill-rule",
    "filter",
    "flood-color",
    "flood-opacity",
    "font-family",
    "font-size",
    "font-size-adjust",
    "font-stretch",
    "font-style",
    "font-variant",
    "font-weight",
    "image-rendering",
    "letter-spacing",
    "lighting-color",
    "marker-end",
    "marker-mid",
    "marker-start",
    "mask",
    "opacity",
    "overflow",
    "paint-order",
    "shape-rendering",
    "stop-color",
    "stop-opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "text-anchor",
    "text-decoration",
    "text-rendering",
    "unicode-bidi",
    "vector-effect",
    "visibility",
    "word-spacing",
    "writing-mode",
}

#: Presentation values identical to the SVG initial value.
DEFAULT_VALUES = {
    "stroke-width": "1",
    "stroke-linecap": "butt",
    "stroke-linejoin": "miter",
    "stroke-miterlimit": "4",
    "fill-opacity": "1",
    "stroke-opacity": "1",
    "opacity": "1",
    "display": "inline",
    "visibility": "visible",
    "fill-rule": "nonzero",
    "clip-rule": "nonzero",
    "font-style": "normal",
    "font-weight": "normal",
    "text-anchor": "start",
    "letter-spacing": "normal",
    "word-spacing": "normal",
}

def _css_text(root: etree._Element) -> str:
    return "\n".join("".join(style.itertext()) for style in collect(root, ["style"]))


def _mirror(root: etree._Element) -> tuple[etree._Element, dict[etree._Element, etree._Element]]:
    """Namespace-free mirror of the tree so cssselect's XPath can match it."""
    mirror_root = etree.Element("svg")
    for key, value in root.attrib.items():
        # rules are routinely scoped to the root id (mermaid emits #my-svg …)
        mirror_root.set(key.split("}", 1)[-1] if "}" in key else key, value)
    mapping: dict[etree._Element, etree._Element] = {}

    def walk(source: etree._Element, target: etree._Element) -> None:
        mapping[target] = source
        for child in source:
            if not isinstance(child.tag, str):
                continue
            clone = etree.SubElement(target, etree.QName(child).localname)
            for key, value in child.attrib.items():
                name = key.split("}", 1)[-1] if "}" in key else key
                clone.set(name, value)
            walk(child, clone)

    walk(root, mirror_root)
    return mirror_root, mapping


def inline_css(root: etree._Element, ctx: PassContext) -> int:
    """Resolve every stylesheet rule onto the matching elements."""
    config = ctx.config
    css = _css_text(root)
    if not css.strip():
        if config.remove_style:
            for style in collect(root, ["style"]):
                style.getparent().remove(style)
        return 0

    mirror, mapping = _mirror(root)
    translator = cssselect.GenericTranslator()
    matches: list[tuple[bool, tuple, int, etree._Element, str, str]] = []
    skipped_rules = 0
    skipped_values = 0
    qualified_rules = 0

    for order, rule in enumerate(
        tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    ):
        if rule.type != "qualified-rule":
            skipped_rules += 1
            continue
        qualified_rules += 1
        declarations = []
        for declaration in tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        ):
            if declaration.type != "declaration":
                continue
            if declaration.lower_name.startswith("--"):
                skipped_values += 1
                continue
            value = tinycss2.serialize(declaration.value).strip()
            if not value or "var(" in value:
                skipped_values += 1
                continue
            if declaration.lower_name in SKIP_PROPERTIES or value in SKIP_VALUES:
                continue
            declarations.append((declaration.lower_name, value, declaration.important))
        if not declarations:
            continue
        try:
            selectors = cssselect.parse(tinycss2.serialize(rule.prelude))
        except Exception:
            skipped_rules += 1
            continue
        for selector in selectors:
            try:
                nodes = mirror.xpath(translator.selector_to_xpath(selector))
            except Exception:
                skipped_rules += 1
                continue
            for node in nodes:
                if not isinstance(node, etree._Element):
                    continue
                original = mapping.get(node)
                if original is None:
                    continue
                for name, value, important in declarations:
                    matches.append(
                        (important, selector.specificity(), order, original, name, value)
                    )

    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    styles: dict[etree._Element, dict[str, str]] = {}
    for _important, _specificity, _order, element, name, value in matches:
        styles.setdefault(element, {})[name] = value

    for element, properties in styles.items():
        properties.update(get_style(element))  # an inline style always wins
        element.set("style", format_style(properties))

    if skipped_rules or skipped_values:
        ctx.warn(
            "styles",
            f"skipped {skipped_rules} at-rule(s) and {skipped_values} unsupported value(s)",
        )

    if config.remove_style:
        if styles or not qualified_rules:
            for style in collect(root, ["style"]):
                style.getparent().remove(style)
        else:
            # deleting a stylesheet nothing matched would silently lose styling
            ctx.warn(
                "styles",
                "no rule could be matched to an element; kept the <style> block",
            )
    return len(styles)


def style_to_attributes(root: etree._Element, ctx: PassContext) -> int:
    """Move presentation properties from ``style`` to plain attributes."""
    moved = 0
    for element in iter_elements(root):
        style = get_style(element)
        if not style:
            continue
        changed = False
        for prop in list(style):
            if prop not in PRESENTATION_ATTRIBUTES:
                continue
            if element.get(prop) is not None:
                continue
            element.set(prop, style.pop(prop))
            moved += 1
            changed = True
        if changed:
            if style:
                element.set("style", format_style(style))
            else:
                element.attrib.pop("style", None)
    return moved


def drop_default_attributes(root: etree._Element, ctx: PassContext) -> int:
    """Remove attributes that only restate the SVG initial value."""
    removed = 0
    for element in iter_elements(root):
        for name, default in DEFAULT_VALUES.items():
            value = element.get(name)
            if value is not None and value.strip().lower() == default:
                del element.attrib[name]
                removed += 1
    return removed


def strip_style_blocks(root: etree._Element, ctx: PassContext) -> int:
    """Remove <style> elements outright."""
    removed = 0
    for style in collect(root, ["style"]):
        style.getparent().remove(style)
        removed += 1
    return removed
