"""Seam: ``registry.rebuild`` over run directories the test writes (a sealed run is produced by ``runs.start`` with the fake experiment)."""

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from research import project, registry, runs
from research.errors import GateError

FAKE = Path(__file__).with_name("fixtures") / "fake_experiment.py"
NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    return project.init_project(tmp_path, "toy", question="q", now=NOW)


def argv(*extra):
    return [sys.executable, str(FAKE), *extra]


def test_rebuild_indexes_sealed_completed_runs_with_decimal_statistics(lay):
    runs.start(lay, "pilot", argv(), seeds=[1, 2, 3], now=NOW)
    reg = registry.rebuild(lay)
    ids = [e["id"] for e in reg["entries"]]
    assert ids == ["r001/baseline/accuracy", "r001/method/accuracy"]
    e = reg["entries"][0]
    assert e["values"] == [{"seed": 1, "value": "0.7100"}, {"seed": 2, "value": "0.7200"}, {"seed": 3, "value": "0.7000"}]
    assert e["statistics"]["n"] == 3 and Decimal(e["statistics"]["mean"]) == Decimal("0.71")
    assert Decimal(e["statistics"]["std"]).quantize(Decimal("0.0001")) == Decimal("0.0100")
    assert e["unit"] == "ratio" and e["class"] == "exploratory" and e["direction"] == "maximize"
    assert reg["inputs"]["r001"]["results_sha256"] and reg["inputs"]["r001"]["prereg"] is None
    assert reg["excluded_runs"] == [] and reg["warnings"] == []
    assert json.loads(lay.registry_json.read_text())["entries"][0]["id"] == "r001/baseline/accuracy"
    assert registry.load(lay)["entries"][0]["id"] == "r001/baseline/accuracy"


def test_unsealed_tampered_and_failed_runs_are_excluded_with_reasons(lay):
    runs.start(lay, "ok", argv(), seeds=[1, 2], now=NOW)
    runs.start(lay, "tampered", argv(), seeds=[1, 2], now=NOW)
    rd = lay.runs / "r002"
    d = json.loads((rd / "results.json").read_text())
    d["observations"][0]["metrics"]["accuracy"] = "0.99"
    (rd / "results.json").write_text(json.dumps(d))
    try:
        runs.start(lay, "failed", argv("--fail"), seeds=[1], now=NOW)
    except Exception:
        pass
    (lay.runs / "r004").mkdir()
    (lay.runs / "r004" / "run.json").write_text(json.dumps({"name": "handmade", "status": "completed", "class": "exploratory"}))
    (lay.runs / "r004" / "results.json").write_text("{}")
    reg = registry.rebuild(lay)
    assert [e["id"].split("/")[0] for e in reg["entries"]] == ["r001", "r001"]
    ex = {r["run_id"]: r["reason"] for r in reg["excluded_runs"]}
    assert "seal" in ex["r002"] and "failed" in ex["r003"] and "seal" in ex["r004"]


def test_sanity_warnings_and_strict(lay):
    runs.start(lay, "same", argv("--same"), seeds=[1, 2], now=NOW)
    reg = registry.rebuild(lay, min_seeds=3)
    kinds = {w["kind"] for w in reg["warnings"]}
    assert {"identical-values-across-conditions", "identical-means", "too-few-seeds"} <= kinds
    with pytest.raises(GateError):
        registry.rebuild(lay, min_seeds=3, strict=True)


def test_missing_metric_def_and_non_finite_values_warn(lay):
    runs.start(lay, "pilot", argv(), seeds=[1, 2], now=NOW)
    rd = lay.runs / "r001"
    d = json.loads((rd / "results.json").read_text())
    d["observations"][0]["metrics"]["extra"] = "nan"
    d["observations"][1]["metrics"]["extra"] = "0.5"
    (rd / "results.json").write_text(json.dumps(d))
    runs.seal(rd)
    reg = registry.rebuild(lay)
    kinds = {w["kind"] for w in reg["warnings"]}
    assert {"missing-metric-def", "non-finite"} <= kinds
    extra = [e for e in reg["entries"] if e["id"].endswith("/extra")][0]
    assert extra["statistics"]["n"] == 1
