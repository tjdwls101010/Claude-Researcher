"""Seam: ``viva.sample`` with an injected RNG seed and ``viva.record`` on stdin-shaped JSON; the gate binds hashes."""

import sys
from pathlib import Path

import pytest

from research import claims, paper, project, registry, runs, viva
from research.errors import GateError, InputError
from savepaper.frontmatter import parse

FAKE = Path(__file__).with_name("fixtures") / "fake_experiment.py"
TEMPLATE = Path(__file__).with_name("fixtures") / "template"
NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    runs.start(lay, "pilot", [sys.executable, str(FAKE)], seeds=[1, 2], now=NOW)
    registry.rebuild(lay)
    for i in range(6):
        claims.add(lay, {"title": f"Claim {i}", "description": "d", "claim_status": "supported", "evidence": [{"registry": "r001/method/accuracy", "statistic": "mean"}]}, kind="hypothesis", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "Dropped", "description": "d"}, kind="alternative", by="claude", now=NOW)
    claims.update(lay, "C07", {"claim_status": "dropped"}, now=NOW)
    paper.init(lay, TEMPLATE, main="main.tex", now=NOW)
    (lay.paper / "sections" / "intro.tex").write_text("i")
    (lay.paper / "sections" / "results.tex").write_text("r")
    return lay


def test_sample_is_seeded_and_adds_a_counterfactual(lay):
    out = viva.sample(lay, n=3, seed=7, now=NOW)
    fm, body = parse((lay.viva / "V01.md").read_text())
    assert fm["type"] == "Viva" and fm["seed"] == 7 and len(fm["questions"]) == 4
    ids = [q["claim"] for q in fm["questions"] if q["kind"] == "claim"]
    assert len(ids) == 3 and "C07" not in ids and fm["questions"][-1]["kind"] == "counterfactual"
    assert fm["inputs"]["draft_sha256"] == paper.draft_hash(lay) and len(fm["inputs"]["claims_sha256"]) == 64 and len(fm["inputs"]["registry_sha256"]) == 64
    again = viva.sample(lay, n=3, seed=7, now=NOW)
    assert [q["claim"] for q in parse((lay.viva / "V02.md").read_text())[0]["questions"]] == [q["claim"] for q in fm["questions"]]
    assert out["id"] == "V01" and "answers first" in out["next"].lower() or "answer" in out["next"].lower()


def test_record_needs_every_answer_before_any_assessment(lay):
    viva.sample(lay, n=2, seed=1, now=NOW)
    qs = parse((lay.viva / "V01.md").read_text())[0]["questions"]
    ids = [q["id"] for q in qs]
    with pytest.raises(InputError) as exc:
        viva.record(lay, "V01", {"answers": [{"id": ids[0], "answer": "..."}], "assessment": []}, now=NOW)
    assert ids[1] in str(exc.value)
    with pytest.raises(InputError):
        viva.record(lay, "V01", {"answers": [{"id": i, "answer": "설명"} for i in ids], "assessment": [{"id": ids[0], "verdict": "pass", "note": ""}]}, now=NOW)
    out = viva.record(lay, "V01", {"answers": [{"id": i, "answer": "설명"} for i in ids], "assessment": [{"id": i, "verdict": "pass" if i != ids[1] else "weak", "note": "n"} for i in ids]}, now=NOW)
    fm, body = parse((lay.viva / "V01.md").read_text())
    assert fm["result"] == "pass" and fm["recorded_at"] == NOW and out["result"] == "pass"
    assert body.index("성진") < body.index("클로드") or body.index("답변") < body.index("평가")
    with pytest.raises(InputError):
        viva.record(lay, "V01", {"answers": [], "assessment": []}, now=NOW)  # already recorded


def test_gate_requires_a_passed_record_bound_to_the_current_hashes(lay):
    viva.sample(lay, n=2, seed=1, now=NOW)
    vid, fs = viva.gate(lay, paper.draft_hash(lay))
    assert vid is None and "record" in fs[0]["message"]
    ids = [q["id"] for q in parse((lay.viva / "V01.md").read_text())[0]["questions"]]
    viva.record(lay, "V01", {"answers": [{"id": i, "answer": "a"} for i in ids], "assessment": [{"id": i, "verdict": "fail", "note": "n"} for i in ids]}, now=NOW)
    vid, fs = viva.gate(lay, paper.draft_hash(lay))
    assert vid is None and "fail" in fs[0]["message"]
    viva.sample(lay, n=2, seed=2, now=NOW)
    ids = [q["id"] for q in parse((lay.viva / "V02.md").read_text())[0]["questions"]]
    viva.record(lay, "V02", {"answers": [{"id": i, "answer": "a"} for i in ids], "assessment": [{"id": i, "verdict": "pass", "note": "n"} for i in ids]}, now=NOW)
    assert viva.gate(lay, paper.draft_hash(lay))[0] == "V02"
    (lay.paper / "sections" / "intro.tex").write_text("changed")
    assert viva.gate(lay, paper.draft_hash(lay))[0] is None


def test_sample_needs_supported_claims(tmp_path):
    lay = project.init_project(tmp_path, "empty", now=NOW)
    paper.init(lay, TEMPLATE, main="main.tex", now=NOW)
    with pytest.raises(GateError):
        viva.sample(lay, n=2, seed=1, now=NOW)
