"""Viva: before submission Claude samples claims, 성진 explains them, and only then does Claude assess. The record binds the draft, claims and registry hashes."""

from __future__ import annotations

import hashlib
import random
import re

from savepaper.frontmatter import dump, parse

from . import claims as claims_mod
from .errors import GateError, InputError, NotFoundError
from .project import Layout, now_iso, write_readme

VERDICTS = ("pass", "weak", "fail")
_VID = re.compile(r"V\d{2,}")


def _hashes(lay: Layout) -> dict:
    from . import paper as paper_mod
    from . import review as review_mod

    return {
        "draft_sha256": paper_mod.draft_hash(lay) if lay.paper.is_dir() else None,
        "claims_sha256": review_mod.claims_hash(lay),
        "registry_sha256": hashlib.sha256(lay.registry_json.read_bytes()).hexdigest() if lay.registry_json.is_file() else None,
    }


def _find(lay: Layout, vid: str):
    if not _VID.fullmatch(vid or ""):
        raise InputError(f"viva id {vid!r} must look like V01")
    path = lay.viva / f"{vid}.md"
    if not path.is_file():
        raise NotFoundError(f"no viva {vid} in {lay.rel(lay.viva)}")
    return path


def sample(lay: Layout, *, n: int = 5, seed: int | None = None, now: str | None = None) -> dict:
    supported = [c for c in claims_mod.list_claims(lay) if c.get("claim_status") == "supported"]
    if not supported:
        raise GateError("viva needs supported claims to sample from (none yet)", findings=[{"severity": "major", "message": "no claim has claim_status: supported", "location": "claims/"}])
    seed = random.randrange(1 << 30) if seed is None else seed
    rng = random.Random(seed)
    picked = rng.sample(supported, min(n, len(supported)))
    questions = [{"id": f"Q{i}", "kind": "claim", "claim": c["id"], "question": f"{c['title']} — 이 주장이 어떤 근거로 성립하는지, 어느 실행의 어느 수치가 뒷받침하는지 설명하라."} for i, c in enumerate(picked, start=1)]
    cf = rng.choice(supported)
    questions.append({"id": f"Q{len(questions) + 1}", "kind": "counterfactual", "claim": cf["id"], "question": f"만약 '{cf['title']}'이 거짓이었다면 레지스트리에서 무엇이 달라 보였겠는가? 그 반대 결과를 이 설계가 잡아낼 수 있었는가?"})
    nums = [int(p.stem[1:]) for p in lay.viva.glob("V*.md") if _VID.fullmatch(p.stem)]
    vid = f"V{(max(nums) + 1 if nums else 1):02d}"
    fm = {
        "type": "Viva",
        "title": f"{vid}: {len(questions)} questions",
        "sampled_at": now or now_iso(),
        "seed": seed,
        "inputs": _hashes(lay),
        "questions": questions,
        "answers": [],
        "answered_at": None,
        "assessment": [],
        "result": None,
        "recorded_at": None,
    }
    path = lay.viva / f"{vid}.md"
    path.write_text(dump(fm, _body(fm)), encoding="utf-8")
    write_readme(lay)
    return {"status": "sampled", "id": vid, "paths": [lay.rel(path)], "questions": questions, "next": "Ask 성진 each question and collect the answers first; assess only after every answer is in, then `viva record` with {answers, assessment}."}


def record(lay: Layout, vid: str, data: dict, *, now: str | None = None) -> dict:
    """Two operations, never one: ``{answers}`` freezes 성진's answers; a later ``{assessment}`` records Claude's verdicts against them."""
    path = _find(lay, vid)
    fm, _ = parse(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InputError("stdin must be a JSON object: {answers: [{id, answer}]} first, then {assessment: [{id, verdict, note}]} in a separate call")
    if "answers" in data and "assessment" in data:
        raise InputError("answers and assessment arrive in separate calls: freeze the answers first, assess afterwards")
    if fm.get("recorded_at"):
        raise InputError(f"{vid} is already recorded; sample a new viva instead")
    qids = [q["id"] for q in fm.get("questions") or []]
    if "answers" in data:
        if fm.get("answered_at"):
            raise InputError(f"{vid} answers were frozen at {fm['answered_at']}; they cannot change")
        answers = {a.get("id"): str(a.get("answer") or "").strip() for a in (data.get("answers") or []) if isinstance(a, dict)}
        missing = [q for q in qids if not answers.get(q)]
        if missing:
            raise InputError(f"answers: 성진's answer is missing for {', '.join(missing)}; every question is answered before anything is assessed")
        fm["answers"] = [{"id": q, "answer": answers[q]} for q in qids]
        fm["answered_at"] = now or now_iso()
        path.write_text(dump(fm, _body(fm)), encoding="utf-8")
        write_readme(lay)
        return {"status": "answered", "id": vid, "paths": [lay.rel(path)], "next": "assess each answer now and `viva record` again with {assessment: [...]}"}
    if "assessment" in data:
        if not fm.get("answered_at"):
            raise InputError("assessment: no answers are frozen yet; record {answers: [...]} first")
        assessment = {a.get("id"): a for a in (data.get("assessment") or []) if isinstance(a, dict)}
        for q in qids:
            a = assessment.get(q)
            if not a or a.get("verdict") not in VERDICTS:
                raise InputError(f"assessment: {q} needs a verdict in {VERDICTS}")
        fm["assessment"] = [{"id": q, "verdict": assessment[q]["verdict"], "note": str(assessment[q].get("note") or "")} for q in qids]
        fm["result"] = "fail" if any(a["verdict"] == "fail" for a in fm["assessment"]) else "pass"
        fm["recorded_at"] = now or now_iso()
        fm["inputs_at_record"] = _hashes(lay)
        path.write_text(dump(fm, _body(fm)), encoding="utf-8")
        write_readme(lay)
        return {"status": "recorded", "id": vid, "result": fm["result"], "paths": [lay.rel(path)]}
    raise InputError("stdin must carry either {answers: [...]} or {assessment: [...]}")


def _body(fm: dict) -> str:
    return (
        "## 질문\n\n" + "\n".join(f"- **{q['id']}** ({q['kind']}, {q['claim']}) {q['question']}" for q in fm["questions"])
        + "\n\n## 성진의 답변\n\n" + ("\n".join(f"- **{a['id']}** {a['answer']}" for a in fm.get("answers") or []) or "_(아직 없음)_")
        + "\n\n## 클로드의 평가\n\n" + ("\n".join(f"- **{a['id']}** {a['verdict']} — {a['note']}" for a in fm.get("assessment") or []) or "_(답변 뒤에)_")
        + (f"\n\n**결과: {fm['result']}**\n" if fm.get("result") else "\n")
    )


def gate(lay: Layout, draft_hash: str) -> tuple[str | None, list[dict]]:
    want = _hashes(lay)
    want["draft_sha256"] = draft_hash
    reasons = []
    vivas = sorted((p.stem, parse(p.read_text(encoding="utf-8"))[0]) for p in lay.viva.glob("V*.md") if _VID.fullmatch(p.stem))
    for vid, fm in reversed(vivas):
        if not fm.get("recorded_at"):
            reasons.append(f"{vid} has no recorded answers (`viva record`)")
            continue
        if fm.get("result") != "pass":
            reasons.append(f"{vid} result is {fm.get('result')}: fail means a claim 성진 could not defend")
            continue
        inp = fm.get("inputs") or {}
        if any(inp.get(k) != want.get(k) for k in ("draft_sha256", "claims_sha256", "registry_sha256")):
            reasons.append(f"{vid} was sampled against a different draft/claims/registry")
            continue
        return vid, []
    msg = f"no passed viva record (`viva sample` then `viva record`) is bound to draft hash {draft_hash[:12]}"
    return None, [{"severity": "major", "message": msg + ("; " + "; ".join(reasons) if reasons else ""), "location": "viva/"}]
