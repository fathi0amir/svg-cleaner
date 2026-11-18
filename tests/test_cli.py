"""Tests for the command line interface."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from svg_cleaner.cli import main

SVG = "http://www.w3.org/2000/svg"
SMALL = (
    f'<svg xmlns="{SVG}" width="10" height="10">'
    '<rect width="10" height="10" display="none"/>'
    '<rect width="5" height="5"/></svg>'
)


def write_svg(tmp_path: Path, name: str = "plot.svg") -> Path:
    path = tmp_path / name
    path.write_text(SMALL, encoding="utf-8")
    return path


class FakeStdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)

    def isatty(self) -> bool:
        return False


def test_cli_writes_a_sibling_with_the_suffix(tmp_path):
    source = write_svg(tmp_path)
    assert main([str(source)]) == 0
    output = tmp_path / "plot.clean.svg"
    assert output.is_file()
    assert "display" not in output.read_text(encoding="utf-8")


def test_cli_explicit_output_and_inplace(tmp_path):
    source = write_svg(tmp_path)
    target = tmp_path / "explicit.svg"
    assert main([str(source), "-o", str(target)]) == 0
    assert target.is_file()

    assert main([str(source), "--inplace"]) == 0
    assert source.read_text(encoding="utf-8").startswith("<?xml")


def test_cli_minify_writes_one_line(tmp_path):
    source = write_svg(tmp_path)
    target = tmp_path / "min.svg"
    assert main([str(source), "-o", str(target), "--minify"]) == 0
    body = target.read_text(encoding="utf-8").split("?>", 1)[1].strip()
    assert "\n" not in body


def test_cli_check_reports_pending_changes(tmp_path):
    source = write_svg(tmp_path)
    assert main(["--check", str(source)]) == 1
    assert main([str(source)]) == 0
    generated = tmp_path / "plot.clean.svg"
    assert main(["--check", str(generated)]) == 0


def test_cli_recursive_directory(tmp_path):
    charts = tmp_path / "charts"
    charts.mkdir()
    write_svg(charts, "a.svg")
    write_svg(charts, "b.svg")
    out = tmp_path / "out"
    assert main(["-r", str(charts), "-o", str(out)]) == 0
    assert sorted(path.name for path in out.iterdir()) == ["a.clean.svg", "b.clean.svg"]


def test_cli_directory_requires_recursive(tmp_path):
    with pytest.raises(SystemExit) as info:
        main([str(tmp_path)])
    assert info.value.code == 2


def test_cli_stdin_to_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", FakeStdin(SMALL.encode("utf-8")))
    target = tmp_path / "stdin.svg"
    assert main(["-", "-o", str(target)]) == 0
    assert target.is_file()


def test_stdout_mode_writes_only_the_document(tmp_path, capfd):
    source = write_svg(tmp_path)
    assert main([str(source), "-o", "-"]) == 0
    captured = capfd.readouterr().out
    assert captured.startswith("<?xml")
    assert captured.rstrip().endswith("</svg>")
    assert "->" not in captured


def test_cli_rejects_empty_suffix(tmp_path):
    source = write_svg(tmp_path)
    with pytest.raises(SystemExit) as info:
        main([str(source), "--suffix", ""])
    assert info.value.code == 2


def test_cli_list_passes(capsys):
    assert main(["--list-passes"]) == 0
    out = capsys.readouterr().out
    assert "clip" in out
    assert "mermaid" in out


def test_cli_rejects_bad_config(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text("[tool.svg-cleaner]\nunknown-option = 1\n", encoding="utf-8")
    source = write_svg(tmp_path)
    assert main([str(source), "--config", str(config)]) == 2


def test_cli_missing_file_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as info:
        main([str(tmp_path / "missing.svg")])
    assert info.value.code == 2


def test_cli_skip_keeps_hidden_elements(tmp_path):
    source = write_svg(tmp_path)
    target = tmp_path / "out.svg"
    assert main([str(source), "-o", str(target), "--skip", "visibility"]) == 0
    body = target.read_text(encoding="utf-8")
    assert 'display="none"' in body


def test_cli_only_runs_named_passes(tmp_path):
    source = write_svg(tmp_path)
    target = tmp_path / "out.svg"
    assert main([str(source), "-o", str(target), "--only", "structure,geometry"]) == 0
    body = target.read_text(encoding="utf-8")
    # hidden rect survives because visibility was skipped
    assert 'display="none"' in body


def test_cli_rejects_unknown_pass(tmp_path):
    source = write_svg(tmp_path)
    with pytest.raises(SystemExit) as info:
        main([str(source), "--only", "nope"])
    assert info.value.code == 2


def test_cli_no_xml_declaration(tmp_path):
    source = write_svg(tmp_path)
    target = tmp_path / "out.svg"
    assert main([str(source), "-o", str(target), "--no-xml-declaration"]) == 0
    assert not target.read_text(encoding="utf-8").startswith("<?xml")


def test_cli_remove_comments_toggle(tmp_path):
    source = tmp_path / "c.svg"
    source.write_text(
        f'<svg xmlns="{SVG}"><!-- keep me --><rect width="1" height="1"/></svg>',
        encoding="utf-8",
    )
    target = tmp_path / "out.svg"
    assert main([str(source), "-o", str(target), "--no-remove-comments"]) == 0
    assert "keep me" in target.read_text(encoding="utf-8")
