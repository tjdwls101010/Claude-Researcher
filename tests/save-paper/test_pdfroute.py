"""Seam: ``pdfroute.pdf_to_markdown`` with the anydoc process injected."""

import subprocess
from pathlib import Path

import pytest

from savepaper.errors import ConvertError
from savepaper.pdfroute import ANYDOC_CMD, KNOWN_LOSSES, pdf_to_markdown


def fake_runner(stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def test_pdf_route_calls_anydoc_and_returns_markdown(tmp_path):
    runner = fake_runner(stdout="# Title\n\n" + "text " * 100)
    md = pdf_to_markdown(tmp_path / "paper.pdf", runner=runner)
    assert md.startswith("# Title")
    assert runner.calls[0][: len(ANYDOC_CMD)] == ANYDOC_CMD
    assert runner.calls[0][-1].endswith("paper.pdf")


def test_pdf_route_failure_is_convert_error(tmp_path):
    with pytest.raises(ConvertError) as exc:
        pdf_to_markdown(tmp_path / "paper.pdf", runner=fake_runner(returncode=1, stderr="boom"))
    assert "boom" in str(exc.value)


def test_pdf_route_flags_scanned_pdf(tmp_path):
    with pytest.raises(ConvertError) as exc:
        pdf_to_markdown(tmp_path / "paper.pdf", runner=fake_runner(stdout="   \n"))
    assert "OCR" in str(exc.value)


def test_known_losses_are_declared():
    assert KNOWN_LOSSES == ["math", "figures"]
