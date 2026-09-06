"""The two hash-bound preconditions: a confirmatory run and the final build.

Each returns what the gate resolved to, or raises ``GateError`` naming every
missing piece. ``runs.py`` and ``paper.py`` call these and never reason about
reviews themselves.
"""

from __future__ import annotations

from . import prereg as prereg_mod
from .errors import GateError, NotFoundError
from .project import Layout


def confirmatory(lay: Layout, prereg_id: str | None) -> tuple[str, str]:
    """(prereg id, design review id) or ``GateError`` naming every missing piece."""
    findings = []
    if not prereg_id:
        findings.append({"severity": "major", "message": "--prereg <PNN> is required for a confirmatory run", "location": "prereg"})
        raise GateError("confirmatory run blocked", findings=findings)
    try:
        prereg_mod.require_clean(lay, prereg_id)
    except GateError as exc:
        findings.extend(exc.findings)
    except NotFoundError as exc:
        findings.append({"severity": "major", "message": str(exc), "location": f"prereg/{prereg_id}.md"})
    from . import review

    review_id, review_findings = review.design_gate(lay, prereg_id)
    findings.extend(review_findings)
    if findings:
        raise GateError(f"confirmatory run blocked: {len(findings)} precondition(s) missing", findings=findings)
    return prereg_id, review_id


def final_build(lay: Layout, draft_hash: str) -> dict:
    from . import review, viva

    findings = []
    review_id, review_findings = review.draft_gate(lay, draft_hash)
    findings.extend(review_findings)
    viva_id, viva_findings = viva.gate(lay, draft_hash)
    findings.extend(viva_findings)
    if findings:
        raise GateError(f"final build blocked: {len(findings)} precondition(s) missing", findings=findings)
    return {"review": review_id, "viva": viva_id}


def readiness(lay: Layout) -> dict:
    """What ``status`` shows: is each gate open now, and if not, why."""
    out = {}
    pid = prereg_mod.latest(lay)
    if pid:
        try:
            confirmatory(lay, pid)
            out["confirmatory"] = {"open": True, "prereg": pid}
        except GateError as exc:
            out["confirmatory"] = {"open": False, "prereg": pid, "missing": [f["message"] for f in exc.findings]}
    else:
        out["confirmatory"] = {"open": False, "prereg": None, "missing": ["no preregistration (`prereg freeze`)"]}
    if (lay.paper / "main.tex").is_file():
        from . import paper as paper_mod

        dh = paper_mod.draft_hash(lay)
        try:
            final_build(lay, dh)
            out["final_build"] = {"open": True, "draft_sha256": dh}
        except GateError as exc:
            out["final_build"] = {"open": False, "draft_sha256": dh, "missing": [f["message"] for f in exc.findings]}
    else:
        out["final_build"] = {"open": False, "draft_sha256": None, "missing": ["no paper/main.tex (`paper init`)"]}
    return out
