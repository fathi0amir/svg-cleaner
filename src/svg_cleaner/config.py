"""Configuration for svg-cleaner passes and the CLI."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .errors import SvgCleanerError

PROFILES = ("auto", "generic", "bokeh", "mermaid")
PATH_MODES = ("keep", "absolute", "relative")
CLUSTER_ALIGNS = ("left", "center", "right")

BOOL_FIELDS = (
    "pretty",
    "xml_declaration",
    "strict",
    "remove_comments",
    "remove_metadata",
    "remove_editor_data",
    "collapse_groups",
    "inline_css",
    "remove_style",
    "style_to_attributes",
    "cleanup_ids",
    "shorten_ids",
    "remove_unused_ids",
    "remove_hidden",
    "trim_clip",
    "trim_dashed",
    "drop_clip",
    "fix_mermaid_labels",
    "convert_foreign_objects",
)
INT_FIELDS = ("precision", "indent")
NUMBER_FIELDS = (
    "baseline",
    "line_height",
    "font_size",
    "cluster_font_size",
    "cluster_pad_top",
    "cluster_pad_x",
    "label_dx",
    "label_dy",
    "clip_inset",
)


@dataclass
class CleanConfig:
    """Every knob of svg-cleaner.

    Defaults match ``svg-cleaner file.svg``: a safe, mostly lossless cleanup.
    """

    # -- profile and output ---------------------------------------------------
    profile: str = "auto"
    pretty: bool = True
    indent: int = 2
    xml_declaration: bool = True
    strict: bool = False

    # -- structure ------------------------------------------------------------
    remove_comments: bool = True
    remove_metadata: bool = True
    remove_editor_data: bool = True
    collapse_groups: bool = True

    # -- styles ---------------------------------------------------------------
    inline_css: bool = True
    remove_style: bool = True
    style_to_attributes: bool = True

    # -- ids ------------------------------------------------------------------
    cleanup_ids: bool = True
    shorten_ids: bool = True
    remove_unused_ids: bool = True

    # -- visibility -----------------------------------------------------------
    remove_hidden: bool = True

    # -- geometry -------------------------------------------------------------
    precision: int = 3
    path_mode: str = "keep"

    # -- clip trimming (the bokeh pass) ---------------------------------------
    trim_clip: bool = True
    #: Dashed strokes keep their dash phase only at the original start, so
    #: trimming them is opt-in: the dashes visibly shift after the cut.
    trim_dashed: bool = False
    drop_clip: bool = False
    clip_inset: float | None = None  # None = half the stroke width

    # -- mermaid --------------------------------------------------------------
    fix_mermaid_labels: bool = True
    convert_foreign_objects: bool = True
    baseline: float = 0.35
    line_height: float = 1.1
    font_size: float | None = None
    cluster_font_size: float | None = None
    cluster_align: str = "center"
    cluster_pad_top: float = 6.0
    cluster_pad_x: float = 8.0
    label_dx: float = 0.0
    label_dy: float = 0.0
    fo_font_family: str | None = None
    fo_fill: str | None = None

    # -- bookkeeping ----------------------------------------------------------
    disabled_passes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in BOOL_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise SvgCleanerError(f"{name} must be true or false")
        for name in INT_FIELDS:
            if type(getattr(self, name)) is not int:
                raise SvgCleanerError(f"{name} must be an integer")
        for name in NUMBER_FIELDS:
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise SvgCleanerError(f"{name} must be a number")
        if not isinstance(self.profile, str):
            raise SvgCleanerError("profile must be a string")
        if self.profile not in PROFILES:
            raise SvgCleanerError(f"unknown profile {self.profile!r}; pick one of {PROFILES}")
        if self.path_mode not in PATH_MODES:
            raise SvgCleanerError(
                f"unknown path mode {self.path_mode!r}; pick one of {PATH_MODES}"
            )
        if self.cluster_align not in CLUSTER_ALIGNS:
            raise SvgCleanerError(
                f"unknown cluster alignment {self.cluster_align!r}; "
                f"pick one of {CLUSTER_ALIGNS}"
            )
        if not 0 <= self.precision <= 12:
            raise SvgCleanerError("precision must be between 0 and 12")
        if self.indent < 0:
            raise SvgCleanerError("indent must not be negative")

    # -- construction ---------------------------------------------------------
    @classmethod
    def field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CleanConfig:
        """Build a config from a TOML table, rejecting unknown keys."""
        known = cls.field_names()
        kwargs: dict[str, Any] = {}
        unknown: list[str] = []
        for raw_key, value in data.items():
            key = str(raw_key).replace("-", "_")
            if key not in known:
                unknown.append(str(raw_key))
                continue
            kwargs[key] = value
        if unknown:
            raise SvgCleanerError(
                "unknown configuration option(s): " + ", ".join(sorted(unknown))
            )
        if "disabled_passes" in kwargs:
            kwargs["disabled_passes"] = tuple(kwargs["disabled_passes"])
        return cls(**kwargs)

    def with_overrides(self, overrides: dict[str, Any]) -> CleanConfig:
        """Return a copy with only the given (non-None) values replaced."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return dataclasses.replace(self, **clean)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def load_config_file(path: Path) -> CleanConfig:
    """Read ``[tool.svg-cleaner]`` (or a flat table) from a TOML file."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SvgCleanerError(f"configuration file not found: {path}") from exc
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SvgCleanerError(f"cannot read configuration file {path}: {exc}") from exc

    for table in (
        raw.get("tool", {}).get("svg-cleaner") if isinstance(raw.get("tool"), dict) else None,
        raw.get("svg-cleaner"),
    ):
        if isinstance(table, dict):
            return CleanConfig.from_dict(table)
    if path.name == "svg-cleaner.toml":
        return CleanConfig.from_dict(raw)
    return CleanConfig()


def find_config(start: Path | None = None) -> CleanConfig | None:
    """Look for ``svg-cleaner.toml`` then ``pyproject.toml`` next to the CWD."""
    base = Path.cwd() if start is None else start
    for name in ("svg-cleaner.toml", "pyproject.toml"):
        candidate = base / name
        if candidate.is_file():
            if name == "pyproject.toml":
                text = candidate.read_text(encoding="utf-8", errors="replace")
                if "svg-cleaner" not in text:
                    continue
            return load_config_file(candidate)
    return None
