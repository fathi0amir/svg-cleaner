"""Public library API: :func:`clean` and :func:`clean_file`."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from .config import CleanConfig
from .document import _source_bytes, atomic_write, dump_svg, iter_elements, load_svg, localname
from .errors import StrictWarning
from .passes import PassContext, run_passes

__all__ = ["CleanResult", "clean", "clean_file", "detect_profile"]


@dataclass
class CleanResult:
    """What one run produced."""

    svg: str
    profile: str
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "changed": self.changed,
            "warnings": list(self.warnings),
            **self.stats,
        }


def detect_profile(root: etree._Element) -> str:
    """Guess ``mermaid``, ``bokeh`` or ``generic`` from the document itself."""
    classes: set[str] = set()
    mermaid_id = False
    bokeh = False
    for element in iter_elements(root):
        element_classes = (element.get("class") or "").split()
        classes.update(element_classes)
        if any(name.startswith("bk-") or name == "bokeh" for name in element_classes):
            bokeh = True
        for key in element.attrib:
            name = key.split("}", 1)[-1]
            if name == "data-bokeh-model-id" or name == "data-bokeh-model":
                bokeh = True
        # Bokeh's SVG backend stamps a transparent paint-order="stroke"
        # background path and a clipPath per plot layer.
        if (
            localname(element) == "path"
            and element.get("paint-order") == "stroke"
            and element.get("fill-opacity") in ("0", "0.0")
        ):
            bokeh = True
        if (element.get("id") or "").startswith("mermaid"):
            mermaid_id = True

    if "mermaid" in classes:
        return "mermaid"
    if (root.get("class") or "").split() == ["mermaid"]:
        return "mermaid"
    if "node" in classes and ({"cluster", "edgeLabel"} & classes):
        return "mermaid"
    if mermaid_id and ("node" in classes or "cluster" in classes or "edgeLabel" in classes):
        return "mermaid"
    if root.get("aria-roledescription") and "node" in classes and mermaid_id:
        return "mermaid"
    if bokeh:
        return "bokeh"
    return "generic"


def clean(
    source: str | bytes,
    config: CleanConfig | None = None,
    *,
    filename: str | None = None,
    only: Iterable[str] | None = None,
    skip: Iterable[str] | None = None,
) -> CleanResult:
    """Clean one SVG document held in memory."""
    settings = config or CleanConfig()
    data = _source_bytes(source)
    selected = tuple(only) if only is not None else None
    skipped = tuple(skip) if skip is not None else ()
    remove_comments = (
        settings.remove_comments
        and (not selected or "structure" in selected)
        and "structure" not in skipped
        and "structure" not in settings.disabled_passes
    )
    root = load_svg(data, remove_comments=remove_comments)
    profile = settings.profile
    if profile == "auto":
        profile = detect_profile(root)

    context = PassContext(config=settings, profile=profile)
    run_passes(root, context, only=selected, skip=skipped)
    output = dump_svg(
        root,
        pretty=settings.pretty,
        indent=settings.indent,
        xml_declaration=settings.xml_declaration,
    )

    if settings.strict and context.warnings:
        raise StrictWarning(context.warnings, filename)

    stats: dict[str, Any] = {
        "bytes_before": len(data),
        "bytes_after": len(output),
        "passes": dict(context.counts),
        "warnings": len(context.warnings),
    }
    return CleanResult(
        svg=output.decode("utf-8"),
        profile=profile,
        stats=stats,
        warnings=list(context.warnings),
        changed=output != data,
    )


def clean_file(
    source: str | Path,
    destination: str | Path | None = None,
    config: CleanConfig | None = None,
    *,
    only: Iterable[str] | None = None,
    skip: Iterable[str] | None = None,
) -> CleanResult:
    """Clean a file and (optionally) write the result atomically."""
    path = Path(source)
    result = clean(path.read_bytes(), config, filename=str(path), only=only, skip=skip)
    if destination is not None:
        atomic_write(Path(destination), result.svg.encode("utf-8"))
    return result
