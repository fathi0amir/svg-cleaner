"""svg-cleaner: clean up SVG files from Python or the command line."""

from __future__ import annotations

from .api import CleanResult, clean, clean_file, detect_profile
from .config import CleanConfig, load_config_file
from .errors import ParseError, SvgCleanerError, UnsupportedSvgError
from .passes import PASSES, describe_passes

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "clean",
    "clean_file",
    "detect_profile",
    "CleanConfig",
    "CleanResult",
    "load_config_file",
    "SvgCleanerError",
    "ParseError",
    "UnsupportedSvgError",
    "PASSES",
    "describe_passes",
]
