"""Seam: the ``save_paper.py`` command line (help, exit codes, offline subcommands)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "save-paper" / "scripts" / "save_paper.py"
FIX = Path(__file__).parent / "fixtures"


def run(*args, **kw):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, **kw)


@pytest.mark.parametrize("cmd", ["save", "batch", "describe", "resolve", "check", "index", "note-check", "doctor"])
def test_every_subcommand_has_help(cmd):
    r = run(cmd, "--help")
    assert r.returncode == 0
    assert r.stdout.startswith("usage: save_paper.py " + cmd)


def test_top_level_help_lists_exit_codes():
    r = run("--help")
    assert r.returncode == 0
    for code in ("0  ok", "3  reference", "6  saved but NOT verified", "7  prerequisites"):
        assert code in r.stdout


def test_usage_error_exits_2():
    assert run("save").returncode == 2
    assert run("nonsense").returncode == 2


def test_doctor_runs():
    r = run("doctor")
    assert r.returncode in (0, 7)
    assert "pandoc" in r.stdout and "pdftoppm" in r.stdout


def test_index_command_writes_readme(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    r = run("index", "--out", str(papers))
    assert r.returncode == 0
    assert (papers / "README.md").read_text().startswith("# Index of Papers")


def test_describe_without_key_exits_7(tmp_path, monkeypatch):
    papers = tmp_path / "papers" / "sources"
    papers.mkdir(parents=True)
    (papers / "1.1.md").write_text("---\ntype: Paper\n---\n\n![](images/x.png)\n")
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT), "describe", "1.1", "--out", str(tmp_path / "papers")], capture_output=True, text=True, env=env)
    assert r.returncode == 7
    assert ".env.example" in r.stderr


def test_note_check_reports_missing_numbers_and_structure(tmp_path):
    source = tmp_path / "2503.17523.md"
    source.write_text(
        "---\ntype: Paper\n---\n\n# Title\n\nAccuracy rose from 42.5% to 87.1% on 624 users in 2025.\n\n$$o^{*}=\\arg\\max r(o)$$\n\n| a | b |\n|---|---|\n| 99.9 | 1 |\n\n## References\n\n- X (2020) 12.3\n"
    )
    good = tmp_path / "note.md"
    good.write_text("---\ntype: Paper Note\nsources:\n  - {id: paper, resource: /papers/sources/2503.17523.md}\n---\n\n# 🖇️제목\n\n" + "정확도는 42.5%에서 87.1%로 올랐고 사용자 624명. $o^{*}=\\arg\\max r(o)$ " + "본문 " * 1500)
    r = run("note-check", "--source", str(source), "--note", str(good))
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(r.stdout)
    assert rep["passed"] and rep["numbers"]["missing"] == [] and rep["equations"]["missing"] == []
    assert "99.9" not in rep["numbers"]["missing"] and rep["numbers"]["total"] == 3  # table + references skipped, year skipped

    bad = tmp_path / "bad.md"
    bad.write_text("---\ntype: Paper Note\n---\n\n# 제목\n\n짧은 노트 87.1%\n")
    r = run("note-check", "--source", str(source), "--note", str(bad))
    assert r.returncode == 6
    rep = json.loads(r.stdout)
    assert rep["numbers"]["missing"] == ["42.5%", "624"]
    assert len(rep["equations"]["missing"]) == 1
    assert any("🖇️" in p for p in rep["problems"]) and any("chars" in p for p in rep["problems"]) and any("sources" in p for p in rep["problems"])
