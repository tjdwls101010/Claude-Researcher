"""Seam: ``runners.run_lane(lane, prompt, schema=..., run=...)`` with the headless CLI injected; mirrors tests/save-paper/test_note.py."""

import json
import subprocess
from pathlib import Path

import pytest

from research import runners

PROJECT = Path(__file__).resolve().parents[2]
SCHEMA = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}


def claude_reply(payload=None, *, structured=True, text=None, code=0, is_error=False, denials=()):
    def run(cmd, **kw):
        out = {"is_error": is_error, "result": text if text is not None else "", "total_cost_usd": 0.25, "num_turns": 3, "permission_denials": list(denials), "modelUsage": {"claude-opus-5": {}}}
        if structured and payload is not None:
            out["structured_output"] = payload
        elif payload is not None:
            out["result"] = json.dumps(payload)
        return subprocess.CompletedProcess(cmd, code, stdout=json.dumps(out), stderr="")

    return run


def codex_reply(payload=None, *, text=None, code=0):
    def run(cmd, **kw):
        last = Path(cmd[cmd.index("-o") + 1])
        last.write_text(json.dumps(payload) if payload is not None else (text or ""))
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")

    return run


def test_claude_lane_uses_agent_schema_and_returns_structured_output(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return claude_reply({"answer": "pong"})(cmd, **kw)

    res = runners.run_lane("claude", "ping", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=run)
    assert res.status == "ok" and res.json == {"answer": "pong"} and res.model == "claude-opus-5" and res.cost_usd == 0.25
    cmd, kw = calls[0]
    assert cmd[:3] == ["claude", "-p", "--agent"] and "critic" in cmd and "--json-schema" in cmd and "--model" not in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA and kw["input"] == "ping" and kw["cwd"] == str(PROJECT)
    assert "Read" in cmd and "Write" not in cmd


def test_claude_lane_falls_back_to_parsing_result_text(tmp_path):
    res = runners.run_lane("claude", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=claude_reply({"answer": "x"}, structured=False))
    assert res.status == "ok" and res.json == {"answer": "x"}
    res = runners.run_lane("claude", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=claude_reply(text="```json\n{\"answer\": \"y\"}\n```"))
    assert res.status == "ok" and res.json == {"answer": "y"}


def test_schema_violation_is_not_ok(tmp_path):
    res = runners.run_lane("claude", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=claude_reply({"nope": 1}))
    assert res.status == "invalid" and "answer" in res.error


@pytest.mark.parametrize(
    "run, status, head",
    [
        (claude_reply(text="", code=1), "failed", "crash"),
        (claude_reply(text="Not logged in. Please run /login", is_error=True), "failed", "auth"),
        (claude_reply(text="I can't help with reviewing this."), "refused", "refused"),
        (claude_reply(text="I read it."), "failed", "nojson"),
    ],
)
def test_claude_failures_are_classified(tmp_path, run, status, head):
    res = runners.run_lane("claude", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=run)
    assert res.status == status and res.error.startswith(head)


def test_codex_lane_pastes_agent_body_and_uses_output_schema(tmp_path):
    calls = []

    seen_schema = {}

    def run(cmd, **kw):
        calls.append((cmd, kw))
        seen_schema.update(json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text()))
        return codex_reply({"answer": "pong"})(cmd, **kw)

    res = runners.run_lane("codex", "ping", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=run)
    assert res.status == "ok" and res.json == {"answer": "pong"} and res.model == "gpt-6-astra"
    cmd, kw = calls[0]
    assert cmd[:2] == ["codex", "exec"] and cmd[cmd.index("-s") + 1] == "read-only" and "--output-schema" in cmd and "--ephemeral" in cmd
    assert seen_schema == SCHEMA
    assert kw["input"].endswith("ping") and "find why" in kw["input"].lower() or "reject" in kw["input"].lower()
    assert not list(tmp_path.glob("*.last.txt")), "temp files are removed"


def test_codex_refusal_and_crash(tmp_path):
    res = runners.run_lane("codex", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=codex_reply(text="I cannot help with that."))
    assert res.status == "refused"
    res = runners.run_lane("codex", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=codex_reply(text="", code=2))
    assert res.status == "failed" and res.error.startswith("crash")


def test_run_with_fallback_switches_lane_once_on_refusal_only(tmp_path):
    calls = []

    def dispatch(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "codex":
            return codex_reply(text="I can't help with that.")(cmd, **kw)
        return claude_reply({"answer": "from claude"})(cmd, **kw)

    res = runners.run_with_fallback("codex", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=dispatch)
    assert calls == ["codex", "claude"] and res.lane == "claude" and res.fallback_from == "codex" and res.json == {"answer": "from claude"}
    calls.clear()

    def crash(cmd, **kw):
        calls.append(cmd[0])
        return codex_reply(text="", code=2)(cmd, **kw)

    res = runners.run_with_fallback("codex", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=crash)
    assert calls == ["codex"] and res.status == "failed", "an environment failure does not fall back"


def test_timeout(tmp_path):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    res = runners.run_lane("claude", "p", schema=SCHEMA, agent="critic", project_root=PROJECT, workdir=tmp_path, run=run, timeout_s=1)
    assert res.status == "failed" and res.error == "timeout"
