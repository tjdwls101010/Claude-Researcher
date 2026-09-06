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
    from savepaper.describe import LOCAL_ENV

    if LOCAL_ENV.exists():
        pytest.skip("a real .env sits beside the code; the no-key path cannot be exercised here")
    papers = tmp_path / "papers" / "sources"
    papers.mkdir(parents=True)
    (papers / "1.1.md").write_text("---\ntype: Paper\n---\n\n![](images/x.png)\n")
    env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT), "describe", "1.1", "--out", str(tmp_path / "papers")], capture_output=True, text=True, env=env)
    assert r.returncode == 7
    assert ".env.example" in r.stderr


def test_save_help_says_alt_text_is_on_by_default():
    r = run("save", "--help")
    assert "--no-describe" in r.stdout
    assert "By default every figure is" in r.stdout  # argparse wraps the rest


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


# --- the note as a pipeline step ------------------------------------------------


def test_help_names_exit_8_and_note_flags():
    assert "8  source saved but the note" in run("--help").stdout
    save_help = run("save", "--help").stdout
    assert "--no-note" in save_help and "--note-runner" in save_help and "{claude,codex}" in save_help
    batch_help = run("batch", "--help").stdout
    assert "--note-concurrency" in batch_help and "--no-note" in batch_help
    note_help = run("note", "--help").stdout
    assert note_help.startswith("usage: save_paper.py note") and "--force" in note_help and "--note-runner" in note_help


def _fake_write_note(monkeypatch, status="written", passed=True):
    import savepaper.note as note_mod

    def fake(source_md, note_md, **kw):
        if status == "written":
            note_md.write_text("---\ntype: Paper Note\n---\n\n# 🖇️x\n")
        return note_mod.NoteResult(status=status, runner=kw.get("runner"), model="claude-opus-5", check={"passed": passed, "numbers": {"missing": []}, "equations": {"missing": []}, "problems": [] if passed else ["x"]}, cost_usd=0.5, path=str(note_md) if status == "written" else None)

    monkeypatch.setattr(note_mod, "write_note", fake)
    return fake


def _saved_source(tmp_path):
    papers = tmp_path / "papers"
    (papers / "sources").mkdir(parents=True)
    (papers / "sources" / "2503.17523.md").write_text("---\ntype: Paper\ntitle: T\narxiv: {id: 2503.17523, version: 3}\nconversion: {route: html, coverage: 1.0}\nverified: {by: x, at: y}\n---\n\n# T\n")
    return papers


def test_note_command_writes_kept_and_force(tmp_path, monkeypatch, capsys):
    import save_paper

    papers = _saved_source(tmp_path)
    _fake_write_note(monkeypatch)
    assert save_paper.main(["note", "2503.17523", "--out", str(papers)]) == 0
    assert "note=written" in capsys.readouterr().out
    assert "2503.17523" in (papers / "README.md").read_text()
    assert save_paper.main(["note", "2503.17523", "--out", str(papers)]) == 0
    assert "note=kept" in capsys.readouterr().out
    assert save_paper.main(["note", "2503.17523", "--out", str(papers), "--force"]) == 0
    assert "note=written" in capsys.readouterr().out
    assert save_paper.main(["note", "9999.99999", "--out", str(papers)]) == 2


def test_note_failure_is_exit_8(tmp_path, monkeypatch, capsys):
    import save_paper

    papers = _saved_source(tmp_path)
    _fake_write_note(monkeypatch, status="failed")
    assert save_paper.main(["note", "2503.17523", "--out", str(papers)]) == 8
    _fake_write_note(monkeypatch, status="written", passed=False)
    assert save_paper.main(["note", "2503.17523", "--out", str(papers), "--force"]) == 8


def test_batch_rows_carry_note_and_summary_counts(tmp_path, monkeypatch, capsys):
    import save_paper
    import savepaper.pipeline as pipeline_mod

    papers = _saved_source(tmp_path)
    (papers / "sources" / "2503.17524.md").write_text((papers / "sources" / "2503.17523.md").read_text().replace("2503.17523", "2503.17524"))
    (papers / "2503.17524.md").write_text("---\ntype: Paper Note\n---\n\n# 🖇️y\n")
    ids = tmp_path / "ids.txt"
    ids.write_text("2503.17523\n2503.17524\nbroken\n")

    def fake_save_one(ref, layout, client, **kw):
        if ref == "broken":
            raise save_paper.SavePaperError("nope")
        return pipeline_mod.Outcome(id=ref, version=3, route="html", coverage=1.0, verified=True, status="saved" if ref.endswith("3") else "up-to-date", path=str(layout.source_md(ref)))

    monkeypatch.setattr(pipeline_mod, "save_one", fake_save_one)
    _fake_write_note(monkeypatch)
    jsonl = tmp_path / "r.jsonl"
    code = save_paper.main(["batch", "--ids-file", str(ids), "--jsonl", str(jsonl), "--out", str(papers), "--note-concurrency", "2"])
    rows = [json.loads(l) for l in jsonl.read_text().splitlines()]
    by_id = {r["id"]: r for r in rows}
    assert code == 1 and by_id["broken"]["status"] == "failed"
    assert by_id["2503.17523"]["note"]["status"] == "written" and by_id["2503.17523"]["exit"] == 0
    assert by_id["2503.17524"]["note"]["status"] == "kept"
    assert "notes: written=1 kept=1 skipped=0 failed=0" in capsys.readouterr().out
    assert "2503.17523" in (papers / "README.md").read_text()
