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


_EXTRA = []  # each stage registers its own section (prereg drift, runs, undispositioned findings, gate readiness)


def register(fn):
    _EXTRA.append(fn)
    return fn


@register
def _prereg_section(lay: Layout) -> dict:
    from . import prereg
    from .errors import NotFoundError

    pid = prereg.latest(lay)
    if pid is None:
        return {"prereg": None}
    try:
        drift = prereg.check(lay, pid)
    except NotFoundError:
        return {"prereg": None}
    return {"prereg": {"id": pid, **drift}}


@register
def _runs_section(lay: Layout) -> dict:
    import json

    from . import runs

    rows = []
    for rd in sorted(p for p in lay.runs.glob("r*") if p.is_dir()) if lay.runs.exists() else []:
        try:
            rj = json.loads((rd / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows.append({"run_id": rd.name, "status": "unreadable"})
            continue
        rows.append({"run_id": rd.name, "name": rj.get("name"), "status": rj.get("status"), "class": rj.get("class"), "sealed": rj.get("status") == "completed" and not runs.verify_seal(rd)})
    reg = None
    if lay.registry_json.is_file():
        try:
            data = json.loads(lay.registry_json.read_text(encoding="utf-8"))
            reg = {"entries": len(data.get("entries", [])), "excluded_runs": len(data.get("excluded_runs", [])), "warnings": len(data.get("warnings", []))}
        except ValueError:
            reg = {"error": "unreadable"}
    return {"runs": rows, "registry": reg}
