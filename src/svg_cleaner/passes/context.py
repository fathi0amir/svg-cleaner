"""Shared plumbing for svg-cleaner passes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from lxml import etree

from ..config import CleanConfig
from ..document import localname


@dataclass
class PassContext:
    """Mutable state shared by the passes for one document."""

    config: CleanConfig
    profile: str = "generic"
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add(self, name: str, count: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + int(count)

    def warn(self, name: str, message: str) -> None:
        self.warnings.append(f"{name}: {message}")


@dataclass(frozen=True)
class Pass:
    """One reversible step of the pipeline."""

    name: str
    description: str
    func: Callable[[etree._Element, PassContext], int]
    #: Config field that toggles the pass (``None`` means always on).
    enabled: str | None = None
    #: Profiles the pass applies to (empty tuple means every profile).
    profiles: tuple[str, ...] = ()


def describe(element: etree._Element) -> str:
    """Short human-readable identity for warnings."""
    identifier = element.get("id")
    name = localname(element) or "node"
    return f"#{identifier}" if identifier else f"<{name}>"
