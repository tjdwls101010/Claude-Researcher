"""Seam: ``research_cli.main(argv)`` with stdin and cwd controlled; the CLI is the interface the skill body points at."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import research_cli

SCRIPT = Path(research_cli.__file__)
NOW = "2026-09-07T01:00:00Z"


def run(capsys, argv, stdin=""):
    old = sys.stdin
    sys.stdin = __import__("io").StringIO(stdin)
    try:
        code = research_cli.main(argv)
    finally:
        sys.stdin = old
    out = capsys.readouterr().out
    return code, (json.loads(out) if "--json" in argv else out)


def test_help_works_from_another_cwd_without_pythonpath(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert proc.returncode == 0 and "Exit codes" in proc.stdout and "decide" in proc.stdout
    for sub in ("init", "status", "decide", "claim", "doctor"):
        p = subprocess.run([sys.executable, str(SCRIPT), sub, "--help"], cwd=tmp_path, capture_output=True, text=True, env=env)
        assert p.returncode == 0, sub


def test_init_status_decide_claim_round_trip(tmp_path, capsys):
    root = ["--root", str(tmp_path), "--json"]
    code, out = run(capsys, root + ["init", "toy", "--question", "Does X beat Y?"])
    assert code == 0 and out["status"] == "ok" and "projects/toy/project.md" in out["paths"]
    code, out = run(capsys, root + ["status", "toy"])
    assert code == 0 and out["phase"] == "exploring" and out["decisions"]["open"] == [] and out["claims"]["candidate"] == []
    proposal = {"title": "베이스라인", "asked": True, "options": [{"label": "A", "fails_when": "x", "evidence": ["/papers/sources/a.md#§1"]}, {"label": "B", "fails_when": "y", "evidence": [], "evidence_gap": "none"}], "recommendation": "A"}
    code, out = run(capsys, root + ["decide", "propose", "toy"], stdin=json.dumps(proposal))
    assert code == 0 and out["id"] == "D001" and out["paths"][0].startswith("projects/toy/decisions/001-")
    code, out = run(capsys, root + ["decide", "propose", "toy"], stdin=json.dumps(dict(proposal, recommendation="Q")))
    assert code == 2 and "recommendation" in out["error"]
    code, out = run(capsys, root + ["status", "toy"])
    assert [d["id"] for d in out["decisions"]["open"]] == ["D001"]
    code, out = run(capsys, root + ["decide", "resolve", "toy", "D001", "--chosen", "B", "--dissent", "A가 낫다"])
    assert code == 0
    code, out = run(capsys, root + ["claim", "add", "toy", "--kind", "hypothesis", "--by", "human:seongjin"], stdin=json.dumps({"title": "X beats Y", "description": "d"}))
    assert code == 0 and out["id"] == "C01"
    code, out = run(capsys, root + ["claim", "update", "toy", "C01"], stdin=json.dumps({"claim_status": "supported", "evidence": [{"registry": "r1/a/acc", "statistic": "mean"}]}))
    assert code == 6 and out["findings"][0]["location"] == "evidence[0]"
    code, out = run(capsys, root + ["status", "toy"])
    assert out["claims"]["candidate"] == ["C01"] and out["decisions"]["agreement"]["human:seongjin"] == {"n": 1, "recommended_chosen": 0}
    assert out["decisions"]["dissent"] == ["D001"]
    code, out = run(capsys, root + ["status", "nope"])
    assert code == 3


def test_human_output_names_status_paths_and_findings(tmp_path, capsys):
    run(capsys, ["--root", str(tmp_path), "init", "toy"])
    code, out = run(capsys, ["--root", str(tmp_path), "status", "toy"])
    assert code == 0 and "phase: exploring" in out


def test_doctor_reports_each_prerequisite(tmp_path, capsys):
    code, out = run(capsys, ["--root", str(tmp_path), "--json", "doctor"])
    names = {c["name"] for c in out["checks"]}
    assert {"tectonic", "codex", "claude", "python:yaml"} <= names
    assert code in (0, 7)
    for c in out["checks"]:
        if not c["ok"]:
            assert "install" in c["detail"] or "->" in c["detail"], c
