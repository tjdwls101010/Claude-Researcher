"""Seam: ``runs.start`` / ``runs.import_run`` / ``runs.verify_seal`` with a real local fake experiment as the child process."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from research import project, runs
from research.errors import GateError, InputError, SubprocessError

FAKE = Path(__file__).with_name("fixtures") / "fake_experiment.py"
NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    return project.init_project(tmp_path, "toy", question="q", now=NOW)


def argv(*extra):
    return [sys.executable, str(FAKE), *extra]


def test_start_runs_argv_without_a_shell_and_seals_the_run(lay, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    res = runs.start(lay, "pilot", argv("--artifact"), seeds=[1, 2, 3], cwd=work, now=NOW)
    assert res["run_id"] == "r001" and res["status"] == "completed" and res["child_exit"] == 0
    rd = lay.runs / "r001"
    rj = json.loads((rd / "run.json").read_text())
    assert rj["argv"][1:] == [str(FAKE), "--artifact"] and rj["cwd"] == str(work) and rj["expected_seeds"] == [1, 2, 3]
    assert rj["prereg"] is None and rj["class"] == "exploratory" and rj["design_review"] is None
    assert rj["started"] == NOW and rj["ended"] and rj["name"] == "pilot"
    assert set(rj["env"]) <= runs.ENV_ALLOWLIST and rj["env"]["RESEARCH_RUN_ID"] == "r001"
    assert "git_sha" in rj and "dirty_diff_sha256" in rj
    log = (rd / "output.log").read_text()
    assert "fake experiment starting" in log and f"cwd {work}" in log
    results = json.loads((rd / "results.json").read_text())
    assert len(results["observations"]) == 6
    seal = json.loads((rd / "seal.json").read_text())
    assert set(seal["files"]) == {"run.json", "results.json", "output.log", "artifacts/curve.csv"}
    assert seal["files"]["results.json"] == hashlib.sha256((rd / "results.json").read_bytes()).hexdigest()
    assert runs.verify_seal(rd) == []
    assert "r001" in lay.readme.read_text()


def test_failure_and_missing_results_are_recorded_and_not_sealed(lay):
    with pytest.raises(SubprocessError) as exc:
        runs.start(lay, "bad", argv("--fail"), seeds=[1], now=NOW)
    assert exc.value.child_exit == 3
    rj = json.loads((lay.runs / "r001" / "run.json").read_text())
    assert rj["status"] == "failed" and rj["child_exit"] == 3 and not (lay.runs / "r001" / "seal.json").exists()
    assert "boom" in (lay.runs / "r001" / "output.log").read_text()
    with pytest.raises(GateError) as exc:
        runs.start(lay, "silent", argv("--no-results"), seeds=[1], now=NOW)
    assert "results.json" in str(exc.value)
    assert json.loads((lay.runs / "r002" / "run.json").read_text())["status"] == "failed"
    with pytest.raises(GateError) as exc:
        runs.start(lay, "schema", argv("--bad-schema"), seeds=[1], now=NOW)
    assert any("observations" in f["location"] for f in exc.value.findings)


def test_timeout_is_interrupted(lay):
    with pytest.raises(SubprocessError) as exc:
        runs.start(lay, "slow", argv("--sleep", "5"), seeds=[1], timeout=0.5, now=NOW)
    assert "timeout" in str(exc.value)
    assert json.loads((lay.runs / "r001" / "run.json").read_text())["status"] == "interrupted"


def test_tampering_after_the_seal_is_detected(lay):
    runs.start(lay, "pilot", argv(), seeds=[1, 2], now=NOW)
    rd = lay.runs / "r001"
    data = json.loads((rd / "results.json").read_text())
    data["observations"][0]["metrics"]["accuracy"] = "0.99"
    (rd / "results.json").write_text(json.dumps(data))
    assert [m["location"] for m in runs.verify_seal(rd)] == ["results.json"]


def test_confirmatory_needs_prereg_and_design_review(lay):
    with pytest.raises(GateError) as exc:
        runs.start(lay, "c", argv(), seeds=[1], confirmatory=True, prereg="P01", now=NOW)
    assert "P01" in str(exc.value) or any("P01" in f["message"] for f in exc.value.findings)
    assert not (lay.runs / "r001").exists(), "a blocked gate never creates a run"


def test_seed_validation(lay):
    with pytest.raises(InputError):
        runs.start(lay, "x", argv(), seeds=[], now=NOW)
    with pytest.raises(InputError):
        runs.start(lay, "x", argv(), seeds=[1, 1], now=NOW)


def write_manifest(src: Path, files):
    rows = []
    for rel in files:
        b = (src / rel).read_bytes()
        rows.append({"path": rel, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()})
    m = src.parent / "manifest.json"
    m.write_text(json.dumps({"files": rows}))
    return m


def make_remote(tmp_path):
    src = tmp_path / "remote" / "out"
    (src / "artifacts").mkdir(parents=True)
    obs = [{"condition": c, "seed": s, "metrics": {"loss": f"{0.5 - 0.01 * s:.3f}"}} for c in ("a", "b") for s in (1, 2)]
    (src / "results.json").write_text(json.dumps({"schema_version": 1, "metric_def": {"loss": {"description": "l", "unit": "nats", "direction": "minimize"}}, "conditions": {"a": {"config_sha256": "a" * 64}, "b": {"config_sha256": "b" * 64}}, "observations": obs}))
    (src / "output.log").write_text("remote log")
    (src / "artifacts" / "w.bin").write_bytes(b"\x00\x01")
    return src


def test_import_checks_manifest_and_publishes_atomically(lay, tmp_path):
    src = make_remote(tmp_path)
    man = write_manifest(src, ["results.json", "output.log", "artifacts/w.bin"])
    res = runs.import_run(lay, src, man, name="gpu-run", seeds=[1, 2], now=NOW)
    rd = lay.runs / res["run_id"]
    rj = json.loads((rd / "run.json").read_text())
    assert rj["status"] == "completed" and rj["imported_from"]["manifest_sha256"] and rj["class"] == "exploratory"
    assert (rd / "artifacts" / "w.bin").read_bytes() == b"\x00\x01" and runs.verify_seal(rd) == []


@pytest.mark.parametrize("corrupt", ["hash", "size", "missing", "escape", "symlink", "absolute", "extra"])
def test_import_refuses_bad_manifests(lay, tmp_path, corrupt):
    src = make_remote(tmp_path)
    files = ["results.json", "output.log", "artifacts/w.bin"]
    man = write_manifest(src, files)
    rows = json.loads(man.read_text())
    if corrupt == "hash":
        (src / "output.log").write_text("edited after manifest")
    elif corrupt == "size":
        rows["files"][1]["size"] += 1
    elif corrupt == "missing":
        (src / "artifacts" / "w.bin").unlink()
    elif corrupt == "escape":
        rows["files"].append({"path": "../secret.txt", "size": 1, "sha256": "0" * 64})
    elif corrupt == "symlink":
        (src / "artifacts" / "link.txt").symlink_to(src / "output.log")
        rows["files"].append({"path": "artifacts/link.txt", "size": 10, "sha256": hashlib.sha256(b"remote log").hexdigest()})
    elif corrupt == "absolute":
        rows["files"].append({"path": str(src / "output.log"), "size": 10, "sha256": hashlib.sha256(b"remote log").hexdigest()})
    elif corrupt == "extra":
        (src / "unlisted.txt").write_text("not in manifest")
    man.write_text(json.dumps(rows))
    with pytest.raises(GateError) as exc:
        runs.import_run(lay, src, man, name="x", seeds=[1, 2], now=NOW)
    assert exc.value.findings, corrupt
    assert not list(lay.runs.iterdir()), "nothing is published on refusal"


def test_seal_covers_every_file_and_rejects_symlinks(lay):
    runs.start(lay, "pilot", argv("--artifact"), seeds=[1], now=NOW)
    rd = lay.runs / "r001"
    (rd / "extra.json").write_text("{}")
    assert [m["location"] for m in runs.verify_seal(rd)] == ["extra.json"]
    (rd / "extra.json").unlink()
    (rd / "artifacts" / "link").symlink_to(rd / "output.log")
    assert any("symlink" in m["message"] for m in runs.verify_seal(rd))
    (rd / "artifacts" / "link").unlink()
    (rd / "seal.json").write_text('{"files": []}')
    assert runs.verify_seal(rd)[0]["location"] == "seal.json"
    (rd / "seal.json").write_text(json.dumps({"files": {"run.json": "0" * 64}}))
    assert any("results.json" in m["message"] for m in runs.verify_seal(rd)), "a seal without the mandatory files is invalid"


def test_argv_secrets_are_redacted_and_code_repo_state_recorded(lay, tmp_path):
    res = runs.start(lay, "p", argv("--conditions", "a,b") + ["--api-key=hunter2", "--token", "abc", "https://u:pw@host/x"], seeds=[1], now=NOW)
    rj = json.loads((lay.runs / res["run_id"] / "run.json").read_text())
    joined = " ".join(rj["argv"])
    assert "hunter2" not in joined and "abc" not in joined.split("--token ")[1][:5] and "u:pw@" not in joined
    assert "[REDACTED]" in joined
    assert "code_git_sha" in rj and "code_dirty_sha256" in rj


def test_import_keeps_producer_run_json_and_rejects_name_aliases(lay, tmp_path):
    src = make_remote(tmp_path)
    (src / "run.json").write_text(json.dumps({"argv": ["python", "train.py"], "git_sha": "abc"}))
    man = write_manifest(src, ["results.json", "output.log", "artifacts/w.bin", "run.json"])
    res = runs.import_run(lay, src, man, name="gpu", seeds=[1, 2], now=NOW)
    rd = lay.runs / res["run_id"]
    assert json.loads((rd / "producer_run.json").read_text())["git_sha"] == "abc"
    assert json.loads((rd / "run.json").read_text())["imported_from"]["producer_run"] == "producer_run.json"
    assert runs.verify_seal(rd) == []
    src2 = make_remote(tmp_path / "two")
    (src2 / "Output.log").write_text("dup")
    man2 = write_manifest(src2, ["results.json", "output.log", "Output.log", "artifacts/w.bin"])
    with pytest.raises(GateError) as exc:
        runs.import_run(lay, src2, man2, name="dup", seeds=[1], now=NOW)
    assert any("alias" in f["message"] or "collide" in f["message"] for f in exc.value.findings)


def test_results_validation_requires_config_hashes_and_survives_bad_types(lay):
    bad = {"schema_version": 1, "metric_def": {"a": {"description": "d", "unit": "u", "direction": "maximize"}}, "conditions": {"c": None}, "observations": [{"condition": ["c"], "seed": [1], "metrics": {"a": "1"}}]}
    f = runs.validate_results(bad)
    assert any("config_sha256" in x["message"] for x in f) and any("seed" in x["message"] for x in f)


def test_missing_prereg_with_confirmatory_is_a_gate_finding(lay):
    with pytest.raises(GateError) as exc:
        runs.start(lay, "c", argv(), seeds=[1], confirmatory=True, now=NOW)
    assert "--prereg" in exc.value.findings[0]["message"]
