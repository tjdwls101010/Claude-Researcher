"""Seam: ``ideate.run_round`` with fake lanes; evidence slices come from ``papers/sources`` files the test writes."""

import json
import subprocess
from pathlib import Path

import pytest

from research import claims, ideate, project
from research.errors import InputError
from savepaper.frontmatter import parse

NOW = "2026-09-07T01:00:00Z"


def make_papers(root: Path):
    src = root / "papers" / "sources"
    src.mkdir(parents=True)
    for i, tags in enumerate((["cs.LG", "scaling"], ["cs.LG", "scaling"], ["cs.CL"]), start=1):
        (src / f"2608.0000{i}.md").write_text(f"---\ntype: Paper\ntitle: Paper {i}\ndescription: Abstract {i}.\ntags: {json.dumps(tags)}\narxiv: {{id: '2608.0000{i}', version: 1}}\n---\n\nbody {i}")


def fake(round1, round2, seen):
    def run(cmd, **kw):
        prompt = kw["input"]
        seen.append((cmd[0], prompt))
        payload = round2 if "ROUND 2" in prompt else round1
        if cmd[0] == "codex":
            Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(payload))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        out = {"is_error": False, "result": "", "structured_output": payload, "total_cost_usd": 0.3, "num_turns": 2, "permission_denials": [], "modelUsage": {"claude-opus-5": {}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    return run


R1 = {"hypotheses": [{"title": "Data scale drives it", "prediction": "Gain grows with tokens", "discriminating_test": "hold model size fixed", "evidence": ["2608.00001"]}]}
R2 = {"critiques": [{"target_lane": "codex-1", "disagreement": "규모가 아니라 데이터 품질", "discriminating_test": "품질 고정 후 규모 변화", "stands": True}]}


def test_two_rounds_with_distinct_slices_and_preserved_disagreements(tmp_path):
    make_papers(tmp_path)
    lay = project.init_project(tmp_path, "toy", question="Why does X help?", now=NOW)
    claims.add(lay, {"title": "X helps via scale", "description": "d"}, kind="hypothesis", by="human:seongjin", now=NOW)
    seen = []
    out = ideate.ideate(lay, question="Why does X help?", lanes={"codex": 2, "claude": 1}, slices=["papers:scaling", "papers:cs.CL", "role:metric-skeptic"], run=fake(R1, R2, seen), now=NOW)
    path = lay.ideation / "I01.md"
    fm, body = parse(path.read_text())
    assert out["id"] == "I01" and fm["type"] == "Ideation" and fm["question"] == "Why does X help?"
    assert [l["lane"] for l in fm["lanes"]] == ["codex-1", "codex-2", "claude-1"]
    assert fm["lanes"][0]["slice"] == "papers:scaling" and fm["lanes"][0]["sources"] == ["2608.00001", "2608.00002"]
    assert fm["lanes"][1]["slice"] == "papers:cs.CL" and fm["lanes"][1]["sources"] == ["2608.00003"]
    assert fm["lanes"][2]["slice"] == "role:metric-skeptic" and fm["lanes"][2]["sources"] == []
    r1 = [p for c, p in seen if "ROUND 1" in p]
    assert len(r1) == 3 and "Paper 1" in r1[0] and "Paper 3" not in r1[0] and "Paper 3" in r1[1]
    assert all("X helps via scale" in p for p in r1), "성진's own hypothesis is in every lane's round 1"
    assert all("Data scale drives it" not in p for p in r1), "round 1 is independent: no other lane's output"
    r2 = [p for c, p in seen if "ROUND 2" in p]
    assert len(r2) == 3 and all("Data scale drives it" in p for p in r2), "round 2 quotes the other lanes' round-1 output"
    assert "## 보존된 의견 차이" in body and "규모가 아니라 데이터 품질" in body and "codex-1" in body
    assert len(fm["disagreements"]) == 3
    assert out["candidates"] == 3 and "claim add --by claude" in out["next"]


def test_lanes_need_an_authored_hypothesis_first(tmp_path):
    make_papers(tmp_path)
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    with pytest.raises(InputError) as exc:
        ideate.ideate(lay, question="q", lanes={"codex": 1}, slices=[], run=fake(R1, R2, []), now=NOW)
    assert "by: human" in str(exc.value) or "human" in str(exc.value)


def test_slice_parsing(tmp_path):
    make_papers(tmp_path)
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    assert ideate.parse_lanes("codex:2,claude:2") == {"codex": 2, "claude": 2}
    with pytest.raises(InputError):
        ideate.parse_lanes("gpt:1")
    assert ideate.resolve_slice(lay, "papers:scaling")["sources"] == ["2608.00001", "2608.00002"]
    with pytest.raises(InputError):
        ideate.resolve_slice(lay, "papers:nothing-has-this-tag")
    with pytest.raises(InputError):
        ideate.resolve_slice(lay, "bogus")


def test_round2_carries_own_position_and_duplicate_slices_are_refused(tmp_path):
    make_papers(tmp_path)
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    claims.add(lay, {"title": "Mine", "description": "d"}, kind="hypothesis", by="human:seongjin", now=NOW)
    seen = []
    ideate.ideate(lay, question="q", lanes={"codex": 2}, slices=["papers:scaling", "role:reviewer"], run=fake(R1, R2, seen), now=NOW)
    r2 = [p for c, p in seen if "ROUND 2" in p]
    assert all("Your own round-1" in p and "Data scale drives it" in p and "Your assignment" in p for p in r2)
    with pytest.raises(InputError) as exc:
        ideate.ideate(lay, question="q", lanes={"codex": 2, "claude": 2}, slices=[], run=fake(R1, R2, []), now=NOW)
    assert "slice" in str(exc.value)
    with pytest.raises(InputError):
        ideate.ideate(lay, question="q", lanes={"codex": 2}, slices=["papers:scaling"], run=fake(R1, R2, []), now=NOW)
