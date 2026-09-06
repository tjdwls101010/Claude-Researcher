"""The decision ledger: ``propose`` records options and a recommendation, ``resolve`` records the choice afterwards.

The two-step file is the mechanism: a recommendation written before the answer
cannot be bent to it, and a decision Claude made alone still leaves a row with
``asked: false``.
"""

from __future__ import annotations

import re
from pathlib import Path

from savepaper.frontmatter import dump, parse

from .errors import InputError, NotFoundError
from .project import Layout, check_slug, now_iso, write_readme

from .meta import DECIDERS  # noqa: E402
MIN_OPTIONS, MAX_OPTIONS = 2, 4
_ID = re.compile(r"^(\d{3})-")


def decision_id(path: Path) -> str:
    m = _ID.match(path.name)
    if not m:
        raise InputError(f"{path.name} is not a decision file (NNN-<slug>.md)")
    return f"D{m.group(1)}"


def _find(lay: Layout, did: str) -> Path:
    m = re.fullmatch(r"D?(\d{3})", did or "")
    if not m:
        raise InputError(f"decision id {did!r} must look like D001")
    hits = sorted(lay.decisions.glob(f"{m.group(1)}-*.md"))
    if not hits:
        raise NotFoundError(f"no decision {did} in {lay.rel(lay.decisions)}")
    return hits[0]


def list_decisions(lay: Layout) -> list[tuple[str, dict, str]]:
    out = []
    for path in sorted(lay.decisions.glob("*.md")):
        if _ID.match(path.name):
            fm, body = parse(path.read_text(encoding="utf-8"))
            out.append((decision_id(path), fm, body))
    return out


def _slugify(title: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or fallback


def validate_proposal(lay: Layout, d: dict) -> None:
    """Every missing or wrong cell is named by its path; the first one found is the message."""
    if not isinstance(d, dict):
        raise InputError("proposal must be a JSON object")
    if not str(d.get("title") or "").strip():
        raise InputError("title: required")
    if not isinstance(d.get("asked"), bool):
        raise InputError("asked: required boolean (true when AskUserQuestion was used)")
    opts = d.get("options")
    if not isinstance(opts, list) or not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
        raise InputError(f"options: {MIN_OPTIONS}-{MAX_OPTIONS} options required")
    labels = []
    for i, o in enumerate(opts):
        if not isinstance(o, dict):
            raise InputError(f"options[{i}]: must be an object")
        label = str(o.get("label") or "").strip()
        if not label:
            raise InputError(f"options[{i}].label: required")
        if label in labels:
            raise InputError(f"options[{i}].label: duplicate label {label!r}")
        labels.append(label)
        o["label"] = label  # persisted exactly as validated
        if not str(o.get("fails_when") or "").strip():
            raise InputError(f"options[{i}].fails_when: required — what breaks if this option is wrong")
        ev = o.get("evidence") or []
        if not isinstance(ev, list):
            raise InputError(f"options[{i}].evidence: must be a list of source/registry references")
        if not ev and not str(o.get("evidence_gap") or "").strip():
            raise InputError(f"options[{i}].evidence_gap: required when evidence is empty (why there is none)")
    if str(d.get("recommendation") or "").strip() not in labels:
        raise InputError(f"recommendation: must be one of {labels}")
    d["recommendation"] = str(d["recommendation"]).strip()
    if d.get("slug") is not None:
        try:
            check_slug(str(d["slug"]))
        except InputError as exc:
            raise InputError(f"slug: {exc}")
    sup = d.get("supersedes")
    if sup is not None:
        known = [did for did, _, _ in list_decisions(lay)]
        if sup not in known:
            raise InputError(f"supersedes: {sup} is not an existing decision ({known or 'none yet'})")


def propose(lay: Layout, d: dict, *, now: str | None = None) -> Path:
    validate_proposal(lay, d)
    nums = [int(_ID.match(p.name).group(1)) for p in lay.decisions.glob("*.md") if _ID.match(p.name)]
    n = (max(nums) + 1) if nums else 1
    slug = d.get("slug") or _slugify(d["title"], "decision")
    path = lay.decisions / f"{n:03d}-{slug}.md"
    fm = {
        "type": "Decision",
        "title": d["title"],
        "proposed_at": now or now_iso(),
        "asked": d["asked"],
        "decided_by": None,
        "options": [
            {"label": o["label"], "fails_when": o["fails_when"], "evidence": list(o.get("evidence") or []), "evidence_gap": o.get("evidence_gap")}
            for o in d["options"]
        ],
        "recommendation": d["recommendation"],
        "chosen": None,
        "resolved_at": None,
        "dissent": None,
        "supersedes": d.get("supersedes"),
    }
    path.write_text(dump(fm, str(d.get("body") or "")), encoding="utf-8")
    write_readme(lay)
    return path


def resolve(lay: Layout, did: str, *, chosen: str, dissent: str | None = None, by: str | None = None, now: str | None = None) -> Path:
    path = _find(lay, did)
    fm, body = parse(path.read_text(encoding="utf-8"))
    if fm.get("chosen") is not None:
        raise InputError(f"{did} was already resolved at {fm.get('resolved_at')} (chosen {fm['chosen']!r}); propose a new decision with supersedes: {did}")
    labels = [o["label"] for o in fm.get("options", [])]
    if chosen not in labels:
        raise InputError(f"chosen: {chosen!r} is not one of {labels}")
    by = by or (DECIDERS[0] if fm.get("asked") else DECIDERS[1])
    if by not in DECIDERS:
        raise InputError(f"by: must be one of {DECIDERS}")
    fm["chosen"] = chosen
    fm["decided_by"] = by
    fm["resolved_at"] = now or now_iso()
    fm["dissent"] = dissent or None
    path.write_text(dump(fm, body), encoding="utf-8")
    write_readme(lay)
    return path


def summary(lay: Layout) -> dict:
    """Open decisions plus the two sycophancy dials: ``asked_ratio`` and recommended=chosen per decider. Measurements, not thresholds."""
    rows = list_decisions(lay)
    open_ = [{"id": did, "title": fm.get("title"), "proposed_at": fm.get("proposed_at")} for did, fm, _ in rows if fm.get("chosen") is None]
    agreement: dict[str, dict] = {}
    for did, fm, _ in rows:
        if fm.get("chosen") is None:
            continue
        a = agreement.setdefault(str(fm.get("decided_by")), {"n": 0, "recommended_chosen": 0})
        a["n"] += 1
        a["recommended_chosen"] += int(fm.get("chosen") == fm.get("recommendation"))
    return {
        "total": len(rows),
        "open": open_,
        "asked_ratio": (sum(1 for _, fm, _ in rows if fm.get("asked")) / len(rows)) if rows else None,
        "agreement": agreement,
        "dissent": [did for did, fm, _ in rows if fm.get("dissent")],
    }
