"""Exceptions raised by :mod:`svg_cleaner`."""

from __future__ import annotations


class SvgCleanerError(Exception):
    """Base class for every error raised by svg-cleaner."""


class ParseError(SvgCleanerError):
    """The input is not well-formed XML or not an SVG document."""


class UnsupportedSvgError(SvgCleanerError):
    """The document uses a feature svg-cleaner refuses to rewrite."""


class StrictWarning(SvgCleanerError):
    """Raised in strict mode when a pass reports a non-fatal warning."""

    def __init__(self, warnings: list[str], source: str | None = None) -> None:
        self.warnings = list(warnings)
        self.source = source
        where = f" in {source}" if source else ""
        detail = "\n  ".join(self.warnings) or "unknown problem"
        super().__init__(f"{len(self.warnings)} warning(s){where}:\n  {detail}")
