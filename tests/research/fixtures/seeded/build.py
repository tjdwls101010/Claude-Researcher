#!/usr/bin/env python3
"""Build the seeded-defect project and its clean control into a root directory, through the real CLI modules.

    python3 tests/research/fixtures/seeded/build.py --root /tmp/eval-root [--which defective|clean|both]

The trees are generated rather than committed because sealed runs carry
hashes and timestamps; ``manifest.json`` beside this file is the list of
defects and stays outside any path a reviewer receives.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / ".claude" / "skills" / "research" / "scripts"))
sys.path.insert(0, str(REPO / ".claude" / "skills" / "save-paper" / "scripts"))

from research import claims, decisions, prereg, project, registry, runs  # noqa: E402

NOW = "2026-09-01T09:00:00Z"
LATER = "2026-09-02T09:00:00Z"
QUESTION = "Does retrieval-augmented prompting improve a 1B-parameter language model's exact-match accuracy on two-hop questions (HotpotQA distractor setting)?"

EXPERIMENT = r'''
import json, os, hashlib
run_dir = os.environ["RESEARCH_RUN_DIR"]; seeds = json.loads(os.environ["RESEARCH_SEEDS"])
same = os.environ.get("SEEDED_SAME_LATENCY") == "1"
obs = []
for ci, c in enumerate(("no_retrieval", "retrieval")):
    for s in seeds:
        acc = 0.412 + 0.015 * ci + 0.004 * (s % 3)
        lat = 118.0 if same else 118.0 + 40.0 * ci + 1.5 * (s % 3)
        obs.append({"condition": c, "seed": s, "metrics": {"exact_match": f"{acc:.3f}", "latency_ms": f"{lat:.1f}"}})
out = {"schema_version": 1,
       "metric_def": {"exact_match": {"description": "exact string match with the gold answer after normalisation", "unit": "ratio", "direction": "maximize"},
                      "latency_ms": {"description": "wall-clock milliseconds per question", "unit": "ms", "direction": "minimize"}},
       "conditions": {c: {"config_sha256": hashlib.sha256(c.encode()).hexdigest()} for c in ("no_retrieval", "retrieval")},
       "observations": obs}
open(os.path.join(run_dir, "results.json"), "w").write(json.dumps(out))
'''


def _decide(lay, title, options, rec, chosen, *, asked, by=None, dissent=None, body=""):
    p = decisions.propose(lay, {"title": title, "asked": asked, "options": options, "recommendation": rec, "body": body}, now=NOW)
    decisions.resolve(lay, decisions.decision_id(p), chosen=chosen, dissent=dissent, by=by, now=LATER)
    return decisions.decision_id(p)


def _run(lay, name, seeds, *, same_latency=False, prereg_id=None):
    import os

    env_key = "SEEDED_SAME_LATENCY"
    old = os.environ.get(env_key)
    os.environ[env_key] = "1" if same_latency else "0"
    try:
        return runs.start(lay, name, [sys.executable, "-c", EXPERIMENT], seeds=seeds, prereg=prereg_id, now=NOW)
    finally:
        if old is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old


def literature(lay, *, honest_boundary: bool):
    from savepaper.frontmatter import dump

    fm = {
        "type": "Literature",
        "title": "rag-2hop literature",
        "entries": [
            {"key": "lewis2020rag", "title": "Retrieval-augmented generation for knowledge-intensive NLP tasks", "verified": {"by": "human:seongjin", "at": NOW}},
            {"key": "yang2018hotpotqa", "title": "HotpotQA: a dataset for diverse, explainable multi-hop question answering", "verified": {"by": "human:seongjin", "at": NOW}},
        ],
        "search_log": [
            "2026-08-30: arXiv cs.CL, query 'retrieval augmented multi-hop small language model', 2025-01..2026-08, 40 abstracts read",
        ] + (["2026-08-31: ACL Anthology 2019-2024, query 'multi-hop retrieval prompting', 25 abstracts read; Semantic Scholar citations of Lewis et al. 2020 filtered by 'small model'"] if honest_boundary else []),
    }
    lay.literature_md.write_text(dump(fm, ""), encoding="utf-8")


def build_defective(root: Path):
    lay = project.init_project(root, "rag-2hop", question=QUESTION, now=NOW)
    literature(lay, honest_boundary=False)
    # C01 hypothesis (edited after freeze -> drift), C02 unfalsifiable prediction, C03 sound prediction, C04 dropped alternative with no test, C05 overclaim by claude
    claims.add(lay, {"title": "Retrieval-augmented prompting raises exact-match accuracy of a 1B model on two-hop questions", "description": "True if the retrieval condition beats no_retrieval on exact_match under the preregistered test.", "body": "The retriever supplies the bridge entity the model cannot recall."}, kind="hypothesis", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The improvement holds in most settings", "description": "True if retrieval helps in most configurations we try.", "body": "Expected from the mechanism."}, kind="prediction", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The gain is larger on questions whose bridge entity is absent from the model's parametric knowledge", "description": "True if the gain on bridge-absent questions exceeds the gain on bridge-present questions.", "body": "Discriminates the retrieval mechanism from a prompt-length effect."}, kind="prediction", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The gain comes from answer leakage: retrieved passages contain the gold answer string verbatim", "description": "True if masking the answer string in retrieved passages removes the gain.", "body": "Contamination alternative."}, kind="alternative", by="claude", now=NOW)
    claims.update(lay, "C04", {"claim_status": "dropped"}, now=LATER)
    claims.add(lay, {"title": "No prior work evaluates retrieval-augmented prompting for two-hop QA on models under 2B parameters", "description": "True if the literature search finds no such paper.", "body": ""}, kind="observation", by="claude", now=NOW)
    # D001 weak baseline against recommendation without dissent; D002 cites a tampered run; D003 money decision made by claude unasked
    _decide(lay, "베이스라인 선택", [
        {"label": "A", "fails_when": "A(최신 self-ask 프롬프팅)가 이미 검색 없이 두 홉을 푸는 경우 우리 효과가 사라진다", "evidence": ["/papers/sources/2608.07885.md#§4.2"]},
        {"label": "B", "fails_when": "B(단순 zero-shot)만 비교하면 리뷰어가 첫 질문으로 self-ask 베이스라인을 요구한다", "evidence": [], "evidence_gap": "구현 시간이 없다"},
    ], "A", "B", asked=True, body="A를 추천했으나 시간 문제로 B를 택했다.")
    _decide(lay, "파일럿 결과 해석", [
        {"label": "A", "fails_when": "r002가 봉인 뒤 수정된 실행이면 근거가 없다", "evidence": ["registry:r002/retrieval/exact_match"]},
        {"label": "B", "fails_when": "파일럿을 다시 돌리면 이틀이 늦어진다", "evidence": [], "evidence_gap": "재실행 전"},
    ], "A", "A", asked=False, body="r002의 exact_match 상승을 근거로 본실험 설계를 확정한다.")
    _decide(lay, "본실험 시드 수", [
        {"label": "A", "fails_when": "2 시드로는 1.5점 차이가 노이즈와 구분되지 않는다", "evidence": [], "evidence_gap": "분산 추정 없음"},
        {"label": "B", "fails_when": "5 시드는 GPU 비용이 2.5배", "evidence": [], "evidence_gap": "비용 추정만"},
    ], "A", "A", asked=False, by="claude", body="GPU 비용을 아끼기 위해 2 시드로 간다.")
    # P01: one-sided test, alpha .10, post-hoc exclusion
    (lay.dir / "analysis.md").write_text("# Analysis plan\n\nOne-sided paired t-test on per-seed exact_match (retrieval > no_retrieval), alpha = 0.10.\nSeeds whose no_retrieval accuracy diverges from the median by more than 2 points are excluded as unstable.\n", encoding="utf-8")
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    claims.update(lay, "C01", {"description": "True if the retrieval condition beats no_retrieval on exact_match OR on F1 under the preregistered test."}, now=LATER)
    # r001: pilot, 2 seeds, identical latency across conditions; r002: tampered after seal
    _run(lay, "pilot", [1, 2], same_latency=True)
    _run(lay, "pilot-2", [1, 2])
    rd = lay.runs / "r002"
    d = json.loads((rd / "results.json").read_text())
    for o in d["observations"]:
        if o["condition"] == "retrieval":
            o["metrics"]["exact_match"] = "0.480"
    (rd / "results.json").write_text(json.dumps(d))
    registry.rebuild(lay)
    return lay


def build_clean(root: Path):
    lay = project.init_project(root, "rag-2hop", question=QUESTION, now=NOW)
    literature(lay, honest_boundary=True)
    claims.add(lay, {"title": "Retrieval-augmented prompting raises exact-match accuracy of a 1B model on two-hop questions", "description": "True if the retrieval condition beats no_retrieval on exact_match under the preregistered two-sided test.", "body": "The retriever supplies the bridge entity the model cannot recall."}, kind="hypothesis", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The gain exceeds 1.5 exact-match points with a 95% CI excluding zero over 5 seeds", "description": "True if the paired difference's CI excludes zero and its mean exceeds 0.015.", "body": "Sized from the pilot variance."}, kind="prediction", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The gain is larger on questions whose bridge entity is absent from the model's parametric knowledge", "description": "True if the gain on bridge-absent questions exceeds the gain on bridge-present questions.", "body": "Discriminates the retrieval mechanism from a prompt-length effect."}, kind="prediction", by="human:seongjin", now=NOW)
    claims.add(lay, {"title": "The gain comes from answer leakage: retrieved passages contain the gold answer string verbatim", "description": "True if masking the answer string in retrieved passages removes the gain.", "body": "Contamination alternative; tested by the masking condition in the confirmatory design (D003)."}, kind="alternative", by="claude", now=NOW)
    claims.add(lay, {"title": "Within arXiv cs.CL 2025-2026 and ACL Anthology 2019-2024, we found no evaluation of retrieval-augmented prompting for two-hop QA on models under 2B parameters", "description": "True relative to the recorded search log.", "body": ""}, kind="observation", by="claude", now=NOW)
    _decide(lay, "베이스라인 선택", [
        {"label": "A", "fails_when": "A(최신 self-ask 프롬프팅)가 이미 검색 없이 두 홉을 푸는 경우 우리 효과가 사라진다", "evidence": ["/papers/sources/2608.07885.md#§4.2"]},
        {"label": "B", "fails_when": "B(단순 zero-shot)만 비교하면 리뷰어가 첫 질문으로 self-ask 베이스라인을 요구한다", "evidence": [], "evidence_gap": "구현 시간이 없다"},
    ], "A", "A", asked=True, body="A를 추천했고 A로 결정.")
    _decide(lay, "파일럿 결과 해석", [
        {"label": "A", "fails_when": "r001 파일럿의 분산이 본실험 크기를 잘못 잡으면 검정력이 부족하다", "evidence": ["registry:r001/retrieval/exact_match"]},
        {"label": "B", "fails_when": "파일럿을 다시 돌리면 이틀이 늦어진다", "evidence": [], "evidence_gap": "재실행 전"},
    ], "A", "A", asked=False, body="r001의 시드 간 분산으로 본실험 시드 수를 정한다.")
    _decide(lay, "본실험 시드 수와 누출 대조 조건", [
        {"label": "A", "fails_when": "5 시드 + 답 마스킹 조건: 비용 2.5배이지만 1.5점 차이를 검정할 검정력이 나온다", "evidence": ["registry:r001/no_retrieval/exact_match"]},
        {"label": "B", "fails_when": "2 시드로는 1.5점 차이가 노이즈와 구분되지 않는다", "evidence": [], "evidence_gap": "검정력 부족"},
    ], "A", "A", asked=True, body="성진이 비용을 승인했다.")
    (lay.dir / "analysis.md").write_text("# Analysis plan\n\nTwo-sided paired t-test on per-seed exact_match (retrieval vs no_retrieval), alpha = 0.05, 5 seeds fixed in advance, no exclusions.\nSecondary: the same test on the answer-masked retrieval condition; the leakage alternative (C04) is refuted if the gain survives masking.\n", encoding="utf-8")
    prereg.freeze(lay, lay.dir / "analysis.md", now=NOW)
    _run(lay, "pilot", [1, 2, 3, 4, 5])
    registry.rebuild(lay)
    return lay


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="directory that will hold projects/<slug>/ (created)")
    ap.add_argument("--which", choices=("defective", "clean", "both"), default="both")
    a = ap.parse_args(argv)
    root = Path(a.root)
    out = {}
    if a.which in ("defective", "both"):
        d = root / "defective"
        (d / "papers" / "sources").mkdir(parents=True, exist_ok=True)
        out["defective"] = str(build_defective(d).dir)
    if a.which in ("clean", "both"):
        c = root / "clean"
        (c / "papers" / "sources").mkdir(parents=True, exist_ok=True)
        out["clean"] = str(build_clean(c).dir)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
