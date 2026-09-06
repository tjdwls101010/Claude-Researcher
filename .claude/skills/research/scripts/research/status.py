"""``status``: the one screen a session reads before doing anything else."""

from __future__ import annotations

from . import claims, decisions
from .project import Layout


def status(lay: Layout) -> dict:
    fm, _ = lay.read_project()
    out = {
        "status": "ok",
        "project": lay.slug,
        "question": fm.get("question"),
        "phase": fm.get("phase"),
        "venue": fm.get("venue"),
        "submission_ready": bool(fm.get("submission_ready")),
        "decisions": decisions.summary(lay),
        "claims": claims.by_status(lay),
    }
    for hook in _EXTRA:
        out.update(hook(lay))
    return out


_EXTRA = []  # later stages register (prereg drift, undispositioned findings, gate readiness) here


def register(fn):
    _EXTRA.append(fn)
    return fn
