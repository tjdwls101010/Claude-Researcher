"""Seam: ``note.write_note(source, target, runner=..., run=...)`` with the headless CLI call injected, plus ``note.note_for`` (when a note is written at all)."""

import json
import subprocess
from pathlib import Path

import pytest

from savepaper.frontmatter import dump, parse
from savepaper.note import NoteResult, write_note

PROJECT = Path(__file__).resolve().parents[2]


def make_source(papers, missing=(), route="html", alts=("desc",)):
    src_dir = papers / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    conv = {"route": route, "coverage": 0.99 if missing else 1.0, "known_losses": ["unparsed math kept as TeX: 2"] + (["coverage check failed: %d block(s) missing" % len(missing)] if missing else [])}
    if missing:
        conv["check"] = {"total": 10, "matched": 10 - len(missing), "missing": [{"kind": "para", "id": f"S{i}.p1", "preview": f"lost paragraph {i} " + "x" * 300} for i in range(len(missing))]}
    fm = {"type": "Paper", "title": "T", "arxiv": {"id": "2503.17523", "version": 3}, "conversion": conv}
    if not missing and route == "html":
        fm["verified"] = {"by": "process:save-paper-check", "at": "2026-09-06T00:00:00Z"}
    body = "# T\n\nIntro with 42.5% result.\n\n" + "\n\n".join(f"![{a}](images/2503.17523v3/fig{i}.png)" for i, a in enumerate(alts, 1))
    md = src_dir / "2503.17523.md"
    md.write_text(dump(fm, body), encoding="utf-8")
    return md


GOOD_NOTE = "---\ntype: Paper Note\ntitle: 제목\nsources:\n  - { id: paper, resource: /papers/sources/2503.17523.md }\ngenerated: { by: paper-note/claude-opus-5, at: 2026-09-06T01:00:00Z }\nstatus: draft\n---\n\n# 🖇️제목\n\n" + "정확도 42.5%. " + "본문 " * 1500


def claude_ok(result_text='{"passed": true, "problems": []}\n\n- chose "attention" untranslated', cost=0.93, model="claude-opus-5", denials=()):
    def run(cmd, **kw):
        # the agent writes the target it was given; find it in the stdin prompt
        prompt = kw["input"]
        target = Path([l for l in prompt.splitlines() if l.startswith("target:")][0].split(": ", 1)[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(GOOD_NOTE, encoding="utf-8")
        out = {"is_error": False, "result": result_text, "total_cost_usd": cost, "num_turns": 17, "permission_denials": list(denials), "modelUsage": {model: {}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    return run


def test_claude_runner_writes_note_atomically_with_facts_in_prompt(tmp_path):
    papers = tmp_path / "papers"
    src = make_source(papers, missing=(1, 2), alts=("", "desc"))
    note = papers / "2503.17523.md"
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return claude_ok()(cmd, **kw)

    res = write_note(src, note, runner="claude", project_root=PROJECT, log=lambda m: None, run=run)
    assert isinstance(res, NoteResult)
    assert res.status == "written" and res.runner == "claude" and res.model == "claude-opus-5"
    assert res.cost_usd == 0.93 and res.turns == 17 and res.check["passed"] is True
    assert res.undecided.strip() == '- chose "attention" untranslated'
    assert note.exists() and not any((papers / ".staging").rglob("*")) if (papers / ".staging").exists() else True
    fm, _ = parse(note.read_text())
    assert fm["generated"]["by"] == "paper-note/claude/claude-opus-5" and fm["generated"]["at"] == "2026-09-06T01:00:00Z"
    cmd, kw = calls[0]
    assert cmd[:3] == ["claude", "-p", "--agent"] and "paper-note" in cmd and "--model" not in cmd
    assert "--output-format" in cmd and "json" in cmd and kw["cwd"] == str(PROJECT)
    prompt = kw["input"]
    assert f"source: {src.resolve()}" in prompt
    assert "target: " in prompt and ".staging/notes/2503.17523.claude.md" in prompt
    # conversion facts travel as data; the agent's rules do not travel at all
    assert "route: html" in prompt and "verified: no" in prompt and "coverage: 0.99" in prompt
    assert "para S0.p1: lost paragraph 0" in prompt and len(prompt) < 6000
    assert "figures with empty alt text: 1" in prompt
    assert "verbatim" not in prompt and "평어체" not in prompt


def claude_reply(text, code=0, is_error=False, denials=(), write=False, stdout=None):
    def run(cmd, **kw):
        if write:
            claude_ok()(cmd, **kw)
        out = stdout if stdout is not None else json.dumps({"is_error": is_error, "result": text, "total_cost_usd": 0.1, "num_turns": 2, "permission_denials": list(denials), "modelUsage": {"claude-opus-5": {}}})
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="boom")

    return run


def codex_ok(body_marker="The principle every rule comes from"):
    def run(cmd, **kw):
        assert cmd[:2] == ["codex", "exec"] and "-s" in cmd and cmd[cmd.index("-s") + 1] == "workspace-write"
        prompt = kw["input"]
        assert body_marker in prompt, "codex gets the agent body pasted in front of the prompt"
        target = Path([l for l in prompt.splitlines() if l.startswith("target:")][0].split(": ", 1)[1])
        target.write_text(GOOD_NOTE, encoding="utf-8")
        Path(cmd[cmd.index("-o") + 1]).write_text('{"passed": true}\nnothing undecided')
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return run


@pytest.mark.parametrize(
    "run, status, error_head, falls_back",
    [
        (claude_reply("", code=1, stdout=""), "failed", "crash", False),
        (claude_reply("", stdout="not json"), "failed", "crash", False),
        (claude_reply("Not logged in. Please run /login", is_error=True), "failed", "auth", False),
        (claude_reply("done", denials=[{"tool_name": "Bash"}]), "failed", "denied", False),
        (claude_reply("I can't help with writing this note."), "refused", "refused", True),
        (claude_reply("I finished reading."), "failed", "nofile", True),
    ],
)
def test_claude_failures_are_classified_and_only_model_failures_fall_back(tmp_path, run, status, error_head, falls_back):
    papers = tmp_path / "papers"
    src = make_source(papers)
    note = papers / "2503.17523.md"
    note.write_text("OLD NOTE")
    calls = []

    def dispatch(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "codex":
            return codex_ok()(cmd, **kw)
        return run(cmd, **kw)

    res = write_note(src, note, runner="claude", project_root=PROJECT, log=lambda m: None, run=dispatch)
    if falls_back:
        assert calls == ["claude", "codex"]
        assert res.status == "written" and res.runner == "codex" and res.fallback_from == "claude"
        assert res.model == "gpt-6-astra" and res.claude_result
        assert parse(note.read_text())[0]["generated"]["by"] == "paper-note/codex/gpt-6-astra"
    else:
        assert calls == ["claude"]
        assert res.status == status and res.error.startswith(error_head), res
        assert note.read_text() == "OLD NOTE", "a failed attempt never touches the existing note"
        assert not res.ok
    assert not (papers / ".staging").exists(), "staging is cleaned either way"


def test_timeout_is_an_environment_failure(tmp_path):
    papers = tmp_path / "papers"
    src = make_source(papers)

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    res = write_note(src, papers / "2503.17523.md", runner="claude", project_root=PROJECT, log=lambda m: None, run=run, timeout_s=1)
    assert res.status == "failed" and res.error == "timeout"


def test_structural_problems_mark_the_note_not_ok_but_keep_it(tmp_path):
    papers = tmp_path / "papers"
    src = make_source(papers)
    note = papers / "2503.17523.md"

    def run(cmd, **kw):
        target = Path([l for l in kw["input"].splitlines() if l.startswith("target:")][0].split(": ", 1)[1])
        target.write_text("---\ntype: Paper Note\n---\n\n# 제목\n\nshort")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"is_error": False, "result": "{}", "modelUsage": {"claude-opus-5": {}}}), stderr="")

    res = write_note(src, note, runner="claude", project_root=PROJECT, log=lambda m: None, run=run)
    assert res.status == "written" and not res.ok and res.check["problems"]
    assert note.exists()


def test_note_for_decision_table(tmp_path):
    from savepaper.note import note_for

    papers = tmp_path / "papers"
    src = make_source(papers)
    note = papers / "2503.17523.md"
    kw = dict(runner="claude", project_root=PROJECT, log=lambda m: None, run=claude_ok())
    assert note_for("saved", note, src, enabled=False, **kw).status == "skipped"
    assert note_for("new-version-available", note, src, **kw).status == "skipped" and not note.exists()
    assert note_for("up-to-date", note, src, **kw).status == "written" and note.exists()
    assert note_for("up-to-date", note, src, **kw).status == "kept"
    assert note_for("saved", note, src, **kw).status == "written"
    assert note_for("up-to-date", note, src, force=True, **kw).status == "written"


def test_denial_with_the_note_present_is_a_warning_not_a_failure(tmp_path):
    papers = tmp_path / "papers"
    src = make_source(papers)
    res = write_note(src, papers / "2503.17523.md", runner="claude", project_root=PROJECT, log=lambda m: None, run=claude_ok(denials=[{"tool_name": "Bash"}]))
    assert res.status == "written" and res.ok and res.denied == ["Bash"]
    assert "denied=Bash" in res.summary()


def test_undecided_survives_a_code_fence_around_the_check_json(tmp_path):
    papers = tmp_path / "papers"
    src = make_source(papers)
    res = write_note(src, papers / "2503.17523.md", runner="claude", project_root=PROJECT, log=lambda m: None, run=claude_ok(result_text='```json\n{"passed": true}\n```\n\n- Figure 4 unreadable'))
    assert res.undecided == "- Figure 4 unreadable"
