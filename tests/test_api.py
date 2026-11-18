"""Tests for the public API."""

from __future__ import annotations

from pathlib import Path

import pytest

from svg_cleaner import CleanConfig, ParseError, clean, clean_file, detect_profile
from svg_cleaner.document import load_svg
from svg_cleaner.errors import StrictWarning

FIXTURES = Path(__file__).parent / "fixtures"
SVG = "http://www.w3.org/2000/svg"


def test_clean_returns_a_result_for_a_string():
    result = clean(f'<svg xmlns="{SVG}"><rect width="10" height="10"/></svg>')
    assert result.profile == "generic"
    assert result.svg.startswith("<?xml")
    assert result.stats["bytes_before"] > 0
    assert result.stats["bytes_after"] == len(result.svg.encode("utf-8"))


def test_bokeh_fixture_is_detected_and_trimmed():
    result = clean_file(FIXTURES / "bokeh_clip.svg")
    assert result.profile == "bokeh"
    assert result.stats["passes"]["clip"] >= 1
    assert "M 50 130" in result.svg
    assert "clip-path" in result.svg  # appearance-preserving default


def test_mermaid_fixture_is_detected_and_repaired():
    result = clean_file(FIXTURES / "mermaid_flow.svg", config=CleanConfig(profile="auto"))
    assert result.profile == "mermaid"
    assert "<foreignObject" not in result.svg
    assert result.stats["passes"]["mermaid"] > 0


def test_inkscape_fixture_loses_editor_data():
    result = clean_file(FIXTURES / "inkscape_export.svg")
    assert "sodipodi" not in result.svg
    assert "inkscape" not in result.svg
    assert "Created with Inkscape" not in result.svg
    assert "<title" not in result.svg


def test_malformed_input_raises_parse_error():
    with pytest.raises(ParseError):
        clean_file(FIXTURES / "malformed.svg")


def test_strict_mode_turns_warnings_into_errors():
    svg = (
        f'<svg xmlns="{SVG}"><clipPath id="c"><rect width="10" height="10"/></clipPath>'
        '<path d="M -20 5 A 5 5 0 0 0 80 5" fill="none" stroke="black" '
        'clip-path="url(#c)"/></svg>'
    )
    with pytest.raises(StrictWarning) as info:
        clean(svg, CleanConfig(strict=True, profile="bokeh"))
    assert any("arc" in warning for warning in info.value.warnings)


def test_clean_file_writes_the_destination(tmp_path):
    source = FIXTURES / "inkscape_export.svg"
    destination = tmp_path / "out.svg"
    result = clean_file(source, destination)
    assert destination.read_text(encoding="utf-8") == result.svg


def test_clean_is_idempotent():
    first = clean_file(FIXTURES / "bokeh_clip.svg")
    second = clean(first.svg)
    assert second.changed is False
    assert second.svg == first.svg


def test_profile_can_be_forced():
    result = clean_file(FIXTURES / "inkscape_export.svg", config=CleanConfig(profile="bokeh"))
    assert result.profile == "bokeh"


def test_detect_profile_reads_bokeh_attributes():
    root = load_svg(
        f'<svg xmlns="{SVG}" data-bokeh-model-id="figure-2"><g class="bk-layer"/></svg>'
    )
    assert detect_profile(root) == "bokeh"


def test_real_bokeh_clip_path_shape_is_trimmed():
    result = clean_file(FIXTURES / "bokeh_clip_path.svg")
    assert result.profile == "bokeh"
    assert result.stats["passes"]["clip"] == 1
    assert "M 85.5" in result.svg
    assert "790.5" in result.svg
    assert result.changed is True
