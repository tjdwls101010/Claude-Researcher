"""Seam: ``claims.add`` / ``claims.update`` / ``claims.list_claims`` on a project under ``tmp_path``; registry and sources are files the test writes."""

import json

import pytest

from research import claims, project
from research.errors import GateError, InputError
from savepaper.frontmatter import parse

NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    return project.init_project(tmp_path, "toy", question="q", now=NOW)


def hyp(**over):
    d = {"title": "Method beats baseline on accuracy", "description": "Method improves accuracy over the baseline on the toy task.", "evidence": [], "body": "Because ..."}
    d.update(over)
    return d


def test_add_writes_an_english_claim_with_immutable_authorship(lay):
    path = claims.add(lay, hyp(), kind="hypothesis", by="human:seongjin", now=NOW)
    assert path.name == "C01.md"
    fm, body = parse(path.read_text())
    assert fm["type"] == "Claim" and fm["kind"] == "hypothesis" and fm["by"] == "human:seongjin"
    assert fm["claim_status"] == "candidate" and fm["prereg"] is None and fm["created"] == NOW and fm["evidence"] == []
    assert "Because" in body
    assert claims.add(lay, hyp(title="second"), kind="prediction", by="claude", now=NOW).name == "C02.md"


@pytest.mark.parametrize(
    "kw, path",
    [
        (dict(kind="guess", by="claude"), "kind"),
        (dict(kind="hypothesis", by="gpt"), "by"),
        (dict(kind="hypothesis", by="claude", d=hyp(title="")), "title"),
        (dict(kind="hypothesis", by="claude", d=hyp(description="")), "description"),
        (dict(kind="hypothesis", by="claude", d=hyp(evidence=[{"registry": "r1/a/acc"}])), "evidence[0].statistic"),
        (dict(kind="hypothesis", by="claude", d=hyp(evidence=[{"source": "/papers/sources/x.md"}])), "evidence[0].locator"),
        (dict(kind="hypothesis", by="claude", d=hyp(evidence=[{"foo": 1}])), "evidence[0]"),
        (dict(kind="hypothesis", by="claude", d=hyp(claim_status="proven")), "claim_status"),
    ],
)
def test_add_names_the_bad_cell(lay, kw, path):
    d = kw.pop("d", hyp())
    with pytest.raises(InputError) as exc:
        claims.add(lay, d, now=NOW, **kw)
    assert path in str(exc.value)


def test_update_merges_fields_and_keeps_by_immutable(lay):
    path = claims.add(lay, hyp(), kind="hypothesis", by="human:seongjin", now=NOW)
    out = claims.update(lay, "C01", {"description": "Sharper.", "prereg": "P01"}, now="2026-09-08T00:00:00Z")
    fm, body = parse(out.read_text())
    assert fm["description"] == "Sharper." and fm["title"] == hyp()["title"] and fm["prereg"] == "P01"
    assert fm["updated"] == "2026-09-08T00:00:00Z" and "Because" in body
    with pytest.raises(InputError) as exc:
        claims.update(lay, "C01", {"by": "claude"}, now=NOW)
    assert "by" in str(exc.value)
    assert parse(path.read_text())[0]["by"] == "human:seongjin"


def test_supported_requires_every_evidence_item_to_resolve(lay, tmp_path):
    claims.add(lay, hyp(), kind="hypothesis", by="human:seongjin", now=NOW)
    ev = [{"registry": "r001/method/accuracy", "statistic": "mean"}, {"source": "/papers/sources/2608.07885.md", "locator": "§4.2"}]
    with pytest.raises(GateError) as exc:
        claims.update(lay, "C01", {"claim_status": "supported", "evidence": ev}, now=NOW)
    msgs = json.dumps(exc.value.findings, ensure_ascii=False)
    assert "r001/method/accuracy" in msgs and "2608.07885" in msgs
    assert parse((lay.claims / "C01.md").read_text())[0]["claim_status"] == "candidate", "a refused transition changes nothing"
    lay.registry_json.write_text(json.dumps({"entries": [{"id": "r001/method/accuracy", "statistics": {"n": 3, "mean": "0.81", "std": "0.01"}}]}))
    (tmp_path / "papers" / "sources").mkdir(parents=True)
    (tmp_path / "papers" / "sources" / "2608.07885.md").write_text("---\ntype: Paper\n---\n\nx")
    out = claims.update(lay, "C01", {"claim_status": "supported", "evidence": ev}, now=NOW)
    assert parse(out.read_text())[0]["claim_status"] == "supported"
    with pytest.raises(GateError):
        claims.update(lay, "C01", {"evidence": [{"registry": "r001/method/accuracy", "statistic": "min"}]}, now=NOW)


def test_dropped_claims_stay_listed(lay):
    claims.add(lay, hyp(), kind="alternative", by="claude", now=NOW)
    claims.update(lay, "C01", {"claim_status": "dropped"}, now=NOW)
    rows = claims.list_claims(lay)
    assert [(c["id"], c["claim_status"]) for c in rows] == [("C01", "dropped")]
    assert claims.by_status(lay)["dropped"] == ["C01"]


@pytest.mark.parametrize("cid", ["../../x/claims/C01", "/tmp/C01", "C01/../C02", "c01", "C"])
def test_claim_ids_are_validated_before_touching_the_filesystem(lay, cid):
    claims.add(lay, hyp(), kind="hypothesis", by="claude", now=NOW)
    with pytest.raises((InputError, __import__("research.errors", fromlist=["NotFoundError"]).NotFoundError)):
        claims.update(lay, cid, {"description": "x"}, now=NOW)


def test_ids_allocate_past_gaps_and_never_overwrite(lay):
    claims.add(lay, hyp(), kind="hypothesis", by="claude", now=NOW)
    (lay.claims / "C01.md").rename(lay.claims / "C07.md")
    assert claims.add(lay, hyp(title="next"), kind="hypothesis", by="claude", now=NOW).name == "C08.md"
    assert parse((lay.claims / "C07.md").read_text())[0]["title"] == hyp()["title"]


@pytest.mark.parametrize("src", [".", ".claude/harness-spec.md", "/papers/sources/../../CLAUDE.md", "/papers/README.md"])
def test_only_files_under_papers_sources_count_as_sources(lay, tmp_path, src):
    (tmp_path / "papers" / "sources").mkdir(parents=True)
    (tmp_path / "papers" / "README.md").write_text("x")
    (tmp_path / "CLAUDE.md").write_text("x")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "harness-spec.md").write_text("x")
    claims.add(lay, hyp(), kind="hypothesis", by="claude", now=NOW)
    with pytest.raises(GateError):
        claims.update(lay, "C01", {"claim_status": "supported", "evidence": [{"source": src, "locator": "§1"}]}, now=NOW)


def test_non_string_fields_and_bad_registry_are_named_not_tracebacks(lay):
    with pytest.raises(InputError) as exc:
        claims.add(lay, hyp(title=123), kind="hypothesis", by="claude", now=NOW)
    assert "title" in str(exc.value)
    claims.add(lay, hyp(), kind="hypothesis", by="claude", now=NOW)
    lay.registry_json.write_text("[]")
    with pytest.raises(GateError) as exc:
        claims.update(lay, "C01", {"claim_status": "supported", "evidence": [{"registry": "r1/a/b", "statistic": "mean"}]}, now=NOW)
    assert "registry" in exc.value.findings[0]["message"]
