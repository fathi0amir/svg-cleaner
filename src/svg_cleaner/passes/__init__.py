"""The ordered pass registry."""

from __future__ import annotations

from collections.abc import Iterable

from lxml import etree

from . import clip, geometry, ids, mermaid, structure, styles, visibility
from .context import Pass, PassContext

__all__ = ["PASSES", "Pass", "PassContext", "describe_passes", "run_passes"]


def _normalize_styles(root: etree._Element, ctx: PassContext) -> int:
    count = 0
    if ctx.config.style_to_attributes:
        count += styles.style_to_attributes(root, ctx)
    count += styles.drop_default_attributes(root, ctx)
    return count


PASSES: list[Pass] = [
    Pass(
        "structure",
        "drop comments, metadata and editor-specific elements/attributes",
        structure.cleanup_document,
    ),
    Pass(
        "styles",
        "inline <style> rules with the real CSS cascade",
        styles.inline_css,
        enabled="inline_css",
    ),
    Pass(
        "style-attributes",
        "move presentation styles to attributes and drop initial values",
        _normalize_styles,
    ),
    Pass(
        "mermaid",
        "convert foreignObject labels and re-place mermaid labels/subgraph titles",
        mermaid.repair_mermaid,
        profiles=("mermaid",),
    ),
    Pass(
        "clip",
        "trim unfilled strokes against rectangular clip paths",
        clip.trim_clipped_strokes,
        enabled="trim_clip",
    ),
    Pass(
        "visibility",
        "remove hidden elements and unreferenced definitions",
        visibility.cleanup_visibility,
    ),
    Pass(
        "geometry",
        "round coordinates, reserialize path data and drop identity transforms",
        geometry.simplify_geometry,
    ),
    Pass(
        "ids",
        "dedupe, shorten and drop ids, rewriting every reference",
        ids.cleanup_ids,
        enabled="cleanup_ids",
    ),
    Pass(
        "collapse-groups",
        "replace redundant single-child <g> wrappers",
        structure.collapse_groups,
        enabled="collapse_groups",
    ),
]


def describe_passes() -> list[tuple[str, str]]:
    return [(item.name, item.description) for item in PASSES]


def run_passes(
    root: etree._Element,
    ctx: PassContext,
    only: Iterable[str] | None = None,
    skip: Iterable[str] | None = None,
) -> dict[str, int]:
    """Run the pipeline in order and return the per-pass counts."""
    selected = set(only) if only else None
    skipped = set(skip) if skip else set()
    skipped.update(ctx.config.disabled_passes)

    for item in PASSES:
        if selected is not None and item.name not in selected:
            continue
        if item.name in skipped:
            continue
        if item.enabled and not getattr(ctx.config, item.enabled):
            continue
        if item.profiles and ctx.profile not in item.profiles:
            continue
        count = item.func(root, ctx) or 0
        ctx.add(item.name, count)
    return ctx.counts

