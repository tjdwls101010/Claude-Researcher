"""Preregistration: freeze the hypotheses, predictions and analysis plan before any result is looked at.

The hash covers a claim's *content* (title, description, kind, by, body), not
its file bytes, so evidence and status can change afterwards without counting
as drift; changing what the claim says does.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from savepaper.frontmatter import dump, parse

from . import claims as claims_mod
from .errors import GateError, InputError, NotFoundError
from .project import Layout, now_iso, write_readme

PREREG_KINDS = ("hypothesis", "prediction")
_PID = re.compile(r"P\d{2,}")


def content_hash(claim: dict) -> str:
    payload = json.dumps({k: claim.get(k) for k in ("title", "description", "kind", "by", "body")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _relevant(lay: Layout) -> list[dict]:
    return [c for c in claims_mod.list_claims(lay) if c.get("kind") in PREREG_KINDS and c.get("claim_status") != "dropped"]


def _find(lay: Layout, pid: str) -> Path:
    if not _PID.fullmatch(pid or ""):
        raise InputError(f"preregistration id {pid!r} must look like P01")
    path = lay.prereg / f"{pid}.md"
    if not path.is_file():
        raise NotFoundError(f"no preregistration {pid} in {lay.rel(lay.prereg)}")
    return path


def latest(lay: Layout) -> str | None:
    ids = sorted((p.stem for p in lay.prereg.glob("P*.md") if _PID.fullmatch(p.stem)), key=lambda s: int(s[1:]))
    return ids[-1] if ids else None


def freeze(lay: Layout, analysis: Path, *, now: str | None = None) -> Path:
    analysis = Path(analysis)
    if not analysis.is_file():
        raise NotFoundError(f"analysis plan {analysis} not found")
    relevant = _relevant(lay)
    if not relevant:
        raise InputError(f"nothing to preregister: no {' or '.join(PREREG_KINDS)} claim that is not dropped")
    plan_bytes = analysis.read_bytes()
    try:
        rel_plan = analysis.resolve().relative_to(lay.dir.resolve()).as_posix()
    except ValueError:
        rel_plan = str(analysis.resolve())  # outside the project: absolute, so `check` finds the same file
    nums = [int(p.stem[1:]) for p in lay.prereg.glob("P*.md") if _PID.fullmatch(p.stem)]
    pid = f"P{(max(nums) + 1 if nums else 1):02d}"
    fm = {
        "type": "Preregistration",
        "title": f"{pid}: {len(relevant)} claim(s) frozen",
        "frozen_at": now or now_iso(),
        "claims": [{"id": c["id"], "kind": c["kind"], "sha256": content_hash(c)} for c in relevant],
        "analysis": {"path": rel_plan, "sha256": hashlib.sha256(plan_bytes).hexdigest()},
    }
    body = ["## Claims (verbatim at freeze time)\n"]
    for c in relevant:
        body.append(f"### {c['id']} ({c['kind']}, {c['by']})\n\n**{c['title']}**\n\n{c['description']}\n\n{c['body']}".rstrip() + "\n")
    body.append(f"## Analysis plan ({rel_plan})\n\n```\n{plan_bytes.decode('utf-8', 'replace').rstrip()}\n```\n")
    path = lay.prereg / f"{pid}.md"
    path.write_text(dump(fm, "\n".join(body)), encoding="utf-8")
    for c in relevant:
        claims_mod.update(lay, c["id"], {"prereg": pid}, now=now)
    write_readme(lay)
    return path


def check(lay: Layout, pid: str | None = None) -> dict:
    """What changed since the freeze: content hash mismatches, new relevant claims, frozen claims gone or dropped, plan bytes."""
    pid = pid or latest(lay)
    if pid is None:
        raise NotFoundError("no preregistration yet (run `prereg freeze` before looking at results)")
    fm, _ = parse(_find(lay, pid).read_text(encoding="utf-8"))
    frozen = {c["id"]: c["sha256"] for c in fm.get("claims", [])}
    current = {c["id"]: c for c in _relevant(lay)}
    changed = [cid for cid, sha in frozen.items() if cid in current and content_hash(current[cid]) != sha]
    removed = [cid for cid in frozen if cid not in current]
    added = [cid for cid in current if cid not in frozen]
    plan = lay.dir / fm["analysis"]["path"]
    plan_changed = (not plan.is_file()) or hashlib.sha256(plan.read_bytes()).hexdigest() != fm["analysis"]["sha256"]
    return {"changed": changed, "added": added, "removed": removed, "analysis_changed": plan_changed}


def require_clean(lay: Layout, pid: str | None = None) -> str:
    """The gate form of ``check``: exit 6 with one finding per drifted item; returns the preregistration id."""
    pid = pid or latest(lay)
    out = check(lay, pid)
    fm, _ = parse(_find(lay, pid).read_text(encoding="utf-8"))
    findings = []
    for cid in out["changed"]:
        findings.append({"severity": "major", "message": f"{cid} changed after {pid} was frozen", "location": f"claims/{cid}.md"})
    for cid in out["added"]:
        findings.append({"severity": "major", "message": f"{cid} is a new hypothesis/prediction not in {pid}", "location": f"claims/{cid}.md"})
    for cid in out["removed"]:
        findings.append({"severity": "major", "message": f"{cid} was in {pid} but is now dropped or missing", "location": f"claims/{cid}.md"})
    if out["analysis_changed"]:
        findings.append({"severity": "major", "message": f"analysis plan changed after {pid} was frozen", "location": fm["analysis"]["path"]})
    if findings:
        raise GateError(f"{pid} has drifted: freeze a new preregistration or revert", findings=findings)
    return pid
