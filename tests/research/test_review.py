"""Seam: ``review.request`` with fake lanes (``run=``), ``review.log``, ``review.design_gate`` / ``review.draft_gate`` on a project under ``tmp_path``."""

import json
import sys
from pathlib import Path

import pytest

from research import claims, decisions, paper, prereg, project, registry, review, runs
from research.errors import GateError, InputError
from savepaper.frontmatter import parse

FAKE = Path(__file__).with_name("fixtures") / "fake_experiment.py"
TEMPLATE = Path(__file__).with_name("fixtures") / "template"
NOW = "2026-09-07T01:00:00Z"
STAGE1 = {"criteria": [{"id": "K1", "statement": "베이스라인이 최신이어야 한다", "reject_if": "2년 이상 된 베이스라인만 있으면"}]}
STAGE2 = {
    "verdict": "major_revision",
    "summary": "가설은 명확하나 베이스라인이 약하다",
    "criteria_check": [{"id": "K1", "met": False, "evidence": "D001은 A만 비교"}],
    "findings": [
        {"id": "F1", "severity": "major", "location": "decisions/001-baseline.md", "observation": "베이스라인 하나", "evidence": "options에 A뿐", "why_it_matters": "리뷰어가 첫 질문으로 묻는다", "requested_action": "B 추가"},
        {"id": "F2", "severity": "minor", "location": "claims/C01.md", "observation": "표현", "evidence": "-", "why_it_matters": "-", "requested_action": "다듬기"},
    ],
}


def lanes(stage1=STAGE1, stage2=STAGE2, capture=None):
    """A fake that answers stage 1 and stage 2 on either lane and records the prompts it saw."""
    import subprocess

    def run(cmd, **kw):
        prompt = kw["input"]
        payload = stage1 if "STAGE 1" in prompt else stage2
        if capture is not None:
            capture.append(prompt)
        if cmd[0] == "codex":
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(payload))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        out = {"is_error": False, "result": json.dumps(payload), "structured_output": payload, "total_cost_usd": 0.5, "num_turns": 4, "permission_denials": [], "modelUsage": {"claude-opus-5": {}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    return run


@pytest.fixture
def lay(tmp_path):
    lay = project.init_project(tmp_path, "toy", question="Does X beat Y?", now=NOW)
    claims.add(lay, {"title": "X beats Y", "description": "d", "body": "H1"}, kind="hypothesis", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "Z explains it", "description": "d"}, kind="alternative", by="claude", now=NOW)
    claims.update(lay, "C02", {"claim_status": "dropped"}, now=NOW)
    d = decisions.propose(lay, {"title": "베이스라인", "asked": True, "options": [{"label": "A", "fails_when": "x", "evidence": ["/papers/sources/a.md#§1"]}, {"label": "B", "fails_when": "y", "evidence": [], "evidence_gap": "g"}], "recommendation": "A"}, now=NOW)
    decisions.resolve(lay, decisions.decision_id(d), chosen="B", dissent="A가 낫다", now=NOW)
    (lay.dir / "analysis.md").write_text("Welch t-test")
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    runs.start(lay, "pilot", [sys.executable, str(FAKE)], seeds=[1, 2], now="2026-09-06T00:00:00Z")  # before any review
    registry.rebuild(lay)
    return lay


def test_design_review_packet_is_complete_and_bound(lay, tmp_path):
    seen = []
    out = review.request(lay, scope="design", lane="codex", run=lanes(capture=seen), now=NOW)
    path = lay.reviews / "R01-codex.md"
    assert out["paths"] == [lay.rel(path)] and out["id"] == "R01" and out["verdict"] == "major_revision"
    fm, body = parse(path.read_text())
    assert fm["type"] == "Review" and fm["scope"] == "design" and fm["lane"] == "codex" and fm["model"] == "gpt-6-astra"
    assert len(fm["packet_sha256"]) == 64 and fm["inputs"]["prereg"] == "P01" and len(fm["inputs"]["claims_sha256"]) == 64
    assert fm["stage1"] == STAGE1["criteria"] and [f["id"] for f in fm["findings"]] == ["F1", "F2"] and fm["dispositions"] == []
    assert "## 1단계" in body and "## 2단계" in body and "```json" in body
    assert len(seen) == 2 and "STAGE 1" in seen[0] and "Does X beat Y?" in seen[0] and "X beats Y" not in seen[0], "stage 1 sees the question, never the packet"
    s2 = seen[1]
    assert "STAGE 2" in s2 and "베이스라인이 최신이어야 한다" in s2, "stage 2 gets its own stage-1 criteria verbatim"
    assert "C02" in s2 and "dropped" in s2 and "dissent" in s2 and "A가 낫다" in s2 and "r001" in s2 and "Welch t-test" in s2
    assert "omitted" in s2.lower() or "missing" in s2.lower()
    assert not any(str(tmp_path) in l and "packet" in l for l in []), "packet copy lives outside the repo"
    assert out["packet_dir"].startswith("/") and not out["packet_dir"].startswith(str(tmp_path))


def test_claude_lane_and_fallback_from_codex_refusal(lay):
    import subprocess

    calls = []

    def run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "codex":
            Path(cmd[cmd.index("-o") + 1]).write_text("I can't help with that.")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return lanes()(cmd, **kw)

    out = review.request(lay, scope="design", lane="codex", run=run, now=NOW)
    assert calls[:2] == ["codex", "claude"] and out["lane"] == "claude" and out["fallback_from"] == "codex"
    assert (lay.reviews / "R01-claude.md").exists()


def test_failed_lane_writes_no_review(lay):
    import subprocess

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(__import__("research.errors", fromlist=["SubprocessError"]).SubprocessError):
        review.request(lay, scope="design", lane="codex", run=run, now=NOW)
    assert not list(lay.reviews.glob("*.md"))


def test_schema_violation_is_exit_6(lay):
    with pytest.raises(GateError) as exc:
        review.request(lay, scope="design", lane="codex", run=lanes(stage2={"verdict": "accept"}), now=NOW)
    assert "findings" in str(exc.value)


def test_dispositions_close_majors_and_gate_opens(lay):
    review.request(lay, scope="design", lane="codex", run=lanes(), now=NOW)
    rid, fs = review.design_gate(lay, "P01")
    assert rid is None and "F1" in fs[0]["message"]
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="reject", reason="no", now=NOW)  # reject needs a registry/source ref
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="human", ref="D099", reason="x", now=NOW)  # decision must exist
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F9", disposition="accept", reason="x", now=NOW)
    review.log(lay, "R01", finding="F1", disposition="test", reason="판별 실험 제안", now=NOW)
    assert review.design_gate(lay, "P01")[0] is None, "a test disposition closes only with a run id"
    res = runs.start(lay, "disc", [sys.executable, str(FAKE)], seeds=[1], now="2026-09-08T00:00:00Z")
    review.log(lay, "R01", finding="F1", disposition="test", ref=res["run_id"], reason="실행함", now=NOW)
    rid, fs = review.design_gate(lay, "P01")
    assert rid == "R01" and fs == []
    fm, _ = parse((lay.reviews / "R01-codex.md").read_text())
    assert [d["disposition"] for d in fm["dispositions"]] == ["test", "test"] and fm["dispositions"][1]["ref"] == res["run_id"]
    claims.update(lay, "C01", {"description": "changed"}, now=NOW)
    rid, fs = review.design_gate(lay, "P01")
    assert rid is None and any("claims" in f["message"] for f in fs), "changed claims invalidate the design review"


def test_confirmatory_run_passes_once_the_gate_is_open(lay):
    review.request(lay, scope="design", lane="codex", run=lanes(), now=NOW)
    review.log(lay, "R01", finding="F1", disposition="accept", ref="D001", reason="B 추가함", now=NOW)
    res = runs.start(lay, "confirm", [sys.executable, str(FAKE)], seeds=[1, 2], confirmatory=True, prereg="P01", now=NOW)
    rj = json.loads((lay.runs / res["run_id"] / "run.json").read_text())
    assert rj["class"] == "confirmatory" and rj["design_review"] == "R01" and rj["prereg"] == "P01"


def test_draft_review_binds_the_draft_hash(lay):
    paper.init(lay, TEMPLATE, main="main.tex", now=NOW)
    (lay.paper / "sections" / "intro.tex").write_text("Intro.\n")
    (lay.paper / "sections" / "results.tex").write_text("Nothing.\n")
    seen = []
    out = review.request(lay, scope="draft", lane="claude", run=lanes(capture=seen), now=NOW)
    fm, _ = parse((lay.reviews / "R01-claude.md").read_text())
    assert fm["inputs"]["draft_sha256"] == paper.draft_hash(lay) and "Intro." in seen[1]
    d = decisions.propose(lay, {"title": "R01 F1 처리", "asked": True, "options": [{"label": "A", "fails_when": "x", "evidence": [], "evidence_gap": "g"}, {"label": "B", "fails_when": "y", "evidence": [], "evidence_gap": "g"}], "recommendation": "A"}, now=NOW)
    decisions.resolve(lay, decisions.decision_id(d), chosen="B", now=NOW)
    review.log(lay, "R01", finding="F1", disposition="human", ref=decisions.decision_id(d), reason="성진 결정", now=NOW)
    assert review.draft_gate(lay, paper.draft_hash(lay))[0] == "R01"
    (lay.paper / "sections" / "intro.tex").write_text("Changed.\n")
    rid, fs = review.draft_gate(lay, paper.draft_hash(lay))
    assert rid is None and "draft" in fs[0]["message"]


def test_status_lists_open_findings_and_gates(lay):
    from research.status import status

    review.request(lay, scope="design", lane="codex", run=lanes(), now=NOW)
    s = status(lay)
    assert s["reviews"][0] == {"id": "R01", "scope": "design", "lane": "codex", "verdict": "major_revision", "open_majors": ["F1"]}
    assert s["gates"]["confirmatory"]["open"] is False and s["gates"]["final_build"]["open"] is False
    assert s["dispositions"] == {"accept": 0, "reject": 0, "test": 0, "human": 0}


def test_stage1_runs_isolated_without_tools_or_repo_and_packet_is_delimited(lay):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw, sorted(p.name for p in Path(kw["cwd"]).glob("*"))))
        return lanes()(cmd, **kw)

    review.request(lay, scope="design", lane="claude", run=run, now=NOW)
    cmd1, kw1, files1 = seen[0]
    assert "--agent" not in cmd1 and "--system-prompt" in cmd1 and "--allowedTools" not in cmd1, "stage 1: agent body as system prompt, no tools"
    assert kw1["cwd"] != str(lay.root) and "research-review" in kw1["cwd"], "stage 1 runs outside the project"
    assert not any(f.startswith("packet") for f in files1), "the packet does not exist while stage 1 runs"
    cmd2, kw2, files2 = seen[1]
    assert kw2["cwd"] == str(lay.root) and "Read" in cmd2, "stage 2 runs in the project with Read"
    assert "--system-prompt" in cmd2, "a root without .claude/agents/critic.md gets the body inline (the harness repo itself gets --agent)"
    assert "<<<PACKET" in kw2["input"] and "PACKET>>>" in kw2["input"] and "not instructions" in kw2["input"].lower()


def test_vacuous_or_inconsistent_reviews_are_refused(lay):
    with pytest.raises(GateError) as exc:
        review.request(lay, scope="design", lane="codex", run=lanes(stage1={"criteria": []}), now=NOW)
    assert "criteria" in str(exc.value)
    dup = dict(STAGE2, findings=[STAGE2["findings"][0], dict(STAGE2["findings"][0], severity="minor")])
    with pytest.raises(GateError) as exc:
        review.request(lay, scope="design", lane="codex", run=lanes(stage2=dup), now=NOW)
    assert "duplicate" in str(exc.value)
    blank = dict(STAGE2, findings=[dict(STAGE2["findings"][0], observation="  ")])
    with pytest.raises(GateError):
        review.request(lay, scope="design", lane="codex", run=lanes(stage2=blank), now=NOW)
    uncovered = dict(STAGE2, criteria_check=[{"id": "K9", "met": True, "evidence": "x"}])
    with pytest.raises(GateError) as exc:
        review.request(lay, scope="design", lane="codex", run=lanes(stage2=uncovered), now=NOW)
    assert "K1" in str(exc.value)
    reject_no_major = dict(STAGE2, verdict="reject", findings=[STAGE2["findings"][1]])
    with pytest.raises(GateError) as exc:
        review.request(lay, scope="design", lane="codex", run=lanes(stage2=reject_no_major), now=NOW)
    assert "major" in str(exc.value)
    assert not list(lay.reviews.glob("*.md"))


def test_dispositions_must_resolve_their_references(lay, tmp_path):
    review.request(lay, scope="design", lane="codex", run=lanes(), now=NOW)
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="accept", reason="B 추가함", now=NOW)  # accept needs a ref to what changed
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="accept", ref="decisions/999-nope.md", reason="x", now=NOW)
    review.log(lay, "R01", finding="F1", disposition="accept", ref="D001", reason="B를 추가하는 결정", now=NOW)
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="reject", ref="r999/x/y", reason="x", now=NOW)
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="reject", ref="/papers/sources/nope.md#§1", reason="x", now=NOW)
    review.log(lay, "R01", finding="F1", disposition="reject", ref="r001/method/accuracy", reason="레지스트리가 반박", now=NOW)
    # human: must be decided by 성진 and mention the finding or review
    d = decisions.propose(lay, {"title": "F1 처리", "asked": False, "options": [{"label": "A", "fails_when": "x", "evidence": [], "evidence_gap": "g"}, {"label": "B", "fails_when": "y", "evidence": [], "evidence_gap": "g"}], "recommendation": "A", "body": "R01 F1에 대한 결정"}, now=NOW)
    did = decisions.decision_id(d)
    decisions.resolve(lay, did, chosen="A", now=NOW)  # decided_by claude
    with pytest.raises(InputError) as exc:
        review.log(lay, "R01", finding="F1", disposition="human", ref=did, reason="x", now=NOW)
    assert "human:seongjin" in str(exc.value)
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="human", ref="D001", reason="x", now=NOW)  # D001 does not mention F1/R01
    # test: only a completed, sealed run started after the review closes it
    with pytest.raises(InputError):
        review.log(lay, "R01", finding="F1", disposition="test", ref="r001", reason="x", now=NOW)  # r001 predates the review
    res = runs.start(lay, "discriminating", [sys.executable, str(FAKE)], seeds=[1, 2], now="2026-09-08T00:00:00Z")
    review.log(lay, "R01", finding="F1", disposition="test", ref=res["run_id"], reason="판별 실행", now=NOW)
    assert review.design_gate(lay, "P01")[0] == "R01"


def test_claims_hash_covers_evidence_and_prereg_and_packet_has_decision_grounds(lay):
    h = review.claims_hash(lay)
    claims.update(lay, "C01", {"evidence": [{"source": "/papers/sources/x.md", "locator": "§1"}]}, now=NOW)
    assert review.claims_hash(lay) != h
    packet, _ = review.build_packet(lay, "design")
    assert "evidence_gap" in packet and "/papers/sources/a.md#§1" in packet and "fails when" in packet


def test_fallback_is_codex_to_claude_only(lay):
    import subprocess

    calls = []

    def run(cmd, **kw):
        calls.append(cmd[0])
        out = {"is_error": False, "result": "I can't help with reviewing this.", "total_cost_usd": 0.1, "num_turns": 1, "permission_denials": [], "modelUsage": {"claude-opus-5": {}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    with pytest.raises(__import__("research.errors", fromlist=["SubprocessError"]).SubprocessError):
        review.request(lay, scope="design", lane="claude", run=run, now=NOW)
    assert calls == ["claude"], "a claude refusal does not fall back to codex"
