"""Seam: ``prereg.freeze`` / ``prereg.check`` on a project under ``tmp_path``; claims are files written through ``claims.add``."""

import pytest

from research import claims, prereg, project
from research.errors import GateError, InputError
from savepaper.frontmatter import parse

NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    claims.add(lay, {"title": "Method beats baseline", "description": "d", "body": "H1"}, kind="hypothesis", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "Gain grows with size", "description": "d", "body": "P1"}, kind="prediction", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "Because of X", "description": "d"}, kind="mechanism", by="claude", now=NOW)
    (lay.dir / "analysis.md").write_text("# Plan\n\nWelch t-test, 5 seeds, alpha 0.05\n")
    return lay


def test_freeze_snapshots_hypotheses_predictions_and_the_plan(lay):
    path = prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    assert path.name == "P01.md"
    fm, body = parse(path.read_text())
    assert fm["type"] == "Preregistration" and fm["frozen_at"] == NOW
    assert [c["id"] for c in fm["claims"]] == ["C01", "C02"] and all(len(c["sha256"]) == 64 for c in fm["claims"])
    assert fm["analysis"]["path"] == "analysis.md" and len(fm["analysis"]["sha256"]) == 64
    assert "Welch t-test" in body and "Method beats baseline" in body and "H1" in body
    assert parse((lay.claims / "C01.md").read_text())[0]["prereg"] == "P01"
    assert parse((lay.claims / "C03.md").read_text())[0]["prereg"] is None, "a mechanism claim is not preregistered"
    assert prereg.check(lay, "P01") == {"changed": [], "added": [], "removed": [], "analysis_changed": False}


def test_freeze_needs_at_least_one_hypothesis_or_prediction(tmp_path):
    lay = project.init_project(tmp_path, "empty", now=NOW)
    (lay.dir / "a.md").write_text("plan")
    with pytest.raises(InputError):
        prereg.freeze(lay, lay.dir / "a.md", now=NOW)


def test_check_reports_changed_added_removed_and_plan_drift(lay):
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    claims.update(lay, "C01", {"description": "sharper"}, now=NOW)  # description is content
    claims.update(lay, "C02", {"claim_status": "dropped"}, now=NOW)
    claims.add(lay, {"title": "New prediction", "description": "d"}, kind="prediction", by="claude", now=NOW)
    (lay.dir / "analysis.md").write_text("changed plan")
    out = prereg.check(lay, "P01")
    assert out == {"changed": ["C01"], "added": ["C04"], "removed": ["C02"], "analysis_changed": True}
    with pytest.raises(GateError) as exc:
        prereg.require_clean(lay, "P01")
    locs = {f["location"] for f in exc.value.findings}
    assert {"claims/C01.md", "claims/C02.md", "claims/C04.md", "analysis.md"} <= locs


def test_status_only_fields_do_not_count_as_drift(lay):
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    claims.update(lay, "C01", {"evidence": [{"source": "/papers/sources/x.md", "locator": "§1"}]}, now=NOW)
    assert prereg.check(lay, "P01")["changed"] == []


def test_latest_prereg_is_the_default(lay):
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    claims.add(lay, {"title": "Another", "description": "d"}, kind="hypothesis", by="human:seongjin", now=NOW)
    assert prereg.freeze(lay, lay.dir / "analysis.md", now=NOW).name == "P02.md"
    assert prereg.latest(lay) == "P02"
    assert prereg.check(lay)["added"] == []
