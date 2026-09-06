"""Structured divergence: lanes with different evidence slices propose hypotheses independently, then critique each other once.

No open chat between models: same-model back-and-forth flatters into
consensus. The ending is not agreement but a discriminating test or 성진's
choice, so disagreements are preserved in the file rather than resolved.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from savepaper.frontmatter import dump, parse

from . import claims as claims_mod
from . import runners
from .errors import GateError, InputError, SubprocessError
from .project import Layout, now_iso, write_readme
from .review import SCHEMA

ROLES = {
    "replicator": "You are the person who will try to reproduce this result next year with a different codebase. What would make it fail to replicate?",
    "metric-skeptic": "You distrust the metric. Which measurement choice could manufacture the effect, and what would show it?",
    "reviewer": "You are the meta-reviewer deciding between this paper and a competitor. What is the alternative explanation the authors have not ruled out?",
}
MAX_SOURCES = 8
MAX_ABSTRACT = 1200


def parse_lanes(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for part in str(text).split(","):
        if not part.strip():
            continue
        try:
            lane, n = part.split(":")
            n = int(n)
        except ValueError:
            raise InputError("--lanes: codex:N,claude:M")
        if lane.strip() not in runners.LANES or n < 1:
            raise InputError(f"--lanes: lane must be one of {runners.LANES} with a positive count")
        out[lane.strip()] = out.get(lane.strip(), 0) + n
    if not out:
        raise InputError("--lanes: at least one lane")
    return out


def resolve_slice(lay: Layout, spec: str) -> dict:
    """``papers:<tag>`` → saved sources carrying that tag (id, title, abstract); ``role:<name>`` → a stance and no sources."""
    if spec.startswith("papers:"):
        tag = spec.split(":", 1)[1].strip()
        rows = []
        for p in sorted(lay.papers_sources.glob("*.md")) if lay.papers_sources.is_dir() else []:
            fm, _ = parse(p.read_text(encoding="utf-8"))
            if tag in [str(t) for t in (fm.get("tags") or [])]:
                rows.append({"id": p.stem, "title": fm.get("title"), "abstract": str(fm.get("description") or "")[:MAX_ABSTRACT], "path": lay.rel(p)})
        if not rows:
            raise InputError(f"slice {spec!r}: no saved source under papers/sources/ carries tag {tag!r}")
        return {"slice": spec, "sources": [r["id"] for r in rows[:MAX_SOURCES]], "rows": rows[:MAX_SOURCES], "role": None}
    if spec.startswith("role:"):
        role = spec.split(":", 1)[1].strip()
        if role not in ROLES:
            raise InputError(f"slice {spec!r}: role must be one of {sorted(ROLES)}")
        return {"slice": spec, "sources": [], "rows": [], "role": ROLES[role]}
    raise InputError(f"slice {spec!r}: use papers:<tag> or role:<{'|'.join(sorted(ROLES))}>")


def _assign(lay: Layout, lanes: dict[str, int], slices: list[str]) -> list[dict]:
    resolved = [resolve_slice(lay, s) for s in slices] or [resolve_slice(lay, f"role:{r}") for r in sorted(ROLES)]
    out = []
    i = 0
    for lane, n in lanes.items():
        for k in range(1, n + 1):
            s = resolved[i % len(resolved)]
            out.append({"lane": f"{lane}-{k}", "runner": lane, **{kk: v for kk, v in s.items()}})
            i += 1
    return out


def _round1_prompt(question: str, human_claims: list[dict], lane: dict) -> str:
    lines = [f"ROUND 1 — independent hypotheses. You are lane {lane['lane']}.", "", f"Research question: {question}", "", "성진's own hypotheses (authored first; do not merely restate them):"]
    lines += [f"- {c['id']}: {c['title']} — {c['description']}" for c in human_claims]
    lines.append("")
    if lane["rows"]:
        lines.append(f"Your evidence slice ({lane['slice']}); read the saved sources if you need more than the abstract:")
        lines += [f"- {r['id']} {r['title']} ({r['path']}): {r['abstract']}" for r in lane["rows"]]
    else:
        lines.append(f"Your stance ({lane['slice']}): {lane['role']}")
    lines += ["", "Propose 2-3 competing hypotheses that differ from each other and from 성진's in what they predict. For each: the prediction, the cheapest experiment that discriminates it from the others, and the evidence you lean on. English titles and predictions; Korean explanation is fine elsewhere."]
    return "\n".join(lines) + "\n"


def _round2_prompt(question: str, lane: dict, others: list[tuple[str, dict]]) -> str:
    lines = [f"ROUND 2 — cross-critique. You are lane {lane['lane']}.", "", f"Research question: {question}", "", "The other lanes proposed (verbatim):"]
    for name, out in others:
        for h in out.get("hypotheses", []):
            lines.append(f"- [{name}] {h['title']} — prediction: {h['prediction']}; test: {h['discriminating_test']}; evidence: {', '.join(h.get('evidence') or []) or 'none'}")
    lines += ["", "For each other lane: where do you disagree, and which single experiment would settle it? `stands: true` if your own round-1 position survives their proposal, false if you concede. Korean. Do not converge for politeness; a disagreement with a discriminating test is the product."]
    return "\n".join(lines) + "\n"


def ideate(lay: Layout, *, question: str, lanes: dict[str, int], slices: list[str], run=subprocess.run, now: str | None = None, timeout_s: float = runners.TIMEOUT_S) -> dict:
    human = [c for c in claims_mod.list_claims(lay) if c.get("by") == "human:seongjin" and c.get("claim_status") != "dropped" and c.get("kind") in ("hypothesis", "prediction", "mechanism")]
    if not human:
        raise InputError("ideate runs after 성진 has written at least one hypothesis (`claim add --by human:seongjin`); lanes anchored on a blank page anchor on each other")
    assigned = _assign(lay, lanes, slices)
    tmp = Path(tempfile.mkdtemp(prefix=f"research-ideate-{lay.slug}-"))
    round1: dict[str, dict] = {}
    round2: dict[str, dict] = {}
    try:
        for lane in assigned:
            res = runners.run_lane(lane["runner"], _round1_prompt(question, human, lane), schema=SCHEMA["ideate_round1"], agent="critic", project_root=lay.root, workdir=tmp / lane["lane"], run=run, timeout_s=timeout_s)
            if not res.ok:
                raise SubprocessError(f"round 1, lane {lane['lane']}: {res.error}; nothing recorded")
            round1[lane["lane"]] = res.json
            lane["model"] = res.model
        for lane in assigned:
            others = [(n, o) for n, o in round1.items() if n != lane["lane"]]
            res = runners.run_lane(lane["runner"], _round2_prompt(question, lane, others), schema=SCHEMA["ideate_round2"], agent="critic", project_root=lay.root, workdir=tmp / lane["lane"], run=run, timeout_s=timeout_s)
            if not res.ok:
                raise SubprocessError(f"round 2, lane {lane['lane']}: {res.error}; nothing recorded")
            round2[lane["lane"]] = res.json
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    nums = [int(p.stem[1:]) for p in lay.ideation.glob("I*.md") if re.fullmatch(r"I\d+", p.stem)]
    iid = f"I{(max(nums) + 1 if nums else 1):02d}"
    disagreements = [{"from": n, **c} for n, out in round2.items() for c in out.get("critiques", [])]
    fm = {
        "type": "Ideation",
        "title": f"{iid}: {question}",
        "question": question,
        "run_at": now or now_iso(),
        "human_claims": [c["id"] for c in human],
        "lanes": [{"lane": l["lane"], "runner": l["runner"], "model": l.get("model"), "slice": l["slice"], "sources": l["sources"]} for l in assigned],
        "round1": round1,
        "round2": round2,
        "disagreements": disagreements,
    }
    body = [f"## 질문\n\n{question}\n", "## 1라운드: 라인별 독립 가설\n"]
    for l in assigned:
        body.append(f"### {l['lane']} ({l['slice']}; sources: {', '.join(l['sources']) or 'none'})\n")
        for h in round1[l["lane"]].get("hypotheses", []):
            body.append(f"- **{h['title']}** — 예측: {h['prediction']}\n  - 판별 실험: {h['discriminating_test']}\n  - 근거: {', '.join(h.get('evidence') or []) or 'none'}")
        body.append("")
    body.append("## 2라운드: 교차 비판\n")
    for l in assigned:
        body.append(f"### {l['lane']}\n")
        for c in round2[l["lane"]].get("critiques", []):
            body.append(f"- → {c['target_lane']}: {c['disagreement']}\n  - 판별 실험: {c['discriminating_test']}\n  - 자기 입장 유지: {'예' if c['stands'] else '아니오(양보)'}")
        body.append("")
    body.append("## 보존된 의견 차이\n")
    body += [f"- {d['from']} → {d['target_lane']}: {d['disagreement']} (판별: {d['discriminating_test']})" for d in disagreements] or ["_(없음 — 라인들이 같은 답으로 접혔다; 증거 조각이 충분히 달랐는지 의심하라)_"]
    body.append("\n## 다음\n\n후보 가설은 `claim add --by claude`로 남기고, 선택은 성진의 `decide propose` → `decide resolve`로 한다. 합의는 종료 조건이 아니다.\n")
    path = lay.ideation / f"{iid}.md"
    path.write_text(dump(fm, "\n".join(body)), encoding="utf-8")
    write_readme(lay)
    candidates = sum(len(o.get("hypotheses", [])) for o in round1.values())
    return {"status": "ideated", "id": iid, "paths": [lay.rel(path)], "candidates": candidates, "disagreements": len(disagreements), "next": "record the candidates worth keeping with `claim add --by claude` (kind hypothesis/alternative) and let 성진 choose through `decide`"}
