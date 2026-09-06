"""Claims: the English sentences that become the paper, each with an author, a kind and the evidence it rests on.

``by`` is immutable because authorship is the boundary 성진 set; ``claim_status``
is named apart from OKF ``status`` so a generic OKF reader does not misread a
scientific verdict as a lifecycle state. A claim is never deleted: ``dropped``
keeps the rejected alternative in every review packet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from savepaper.frontmatter import dump, parse

from .errors import GateError, InputError, NotFoundError
from .project import Layout, now_iso, write_readme

from .meta import AUTHORS, KINDS, STATISTICS  # noqa: E402
from .meta import CLAIM_STATUSES as STATUSES  # noqa: E402
_ID = re.compile(r"^C(\d{2,})\.md$")
_CID = re.compile(r"C\d{2,}")


def _find(lay: Layout, cid: str) -> Path:
    if not _CID.fullmatch(cid or ""):
        raise InputError(f"claim id {cid!r} must look like C01")
    path = lay.claims / f"{cid}.md"
    if not path.is_file():
        raise NotFoundError(f"no claim {cid} in {lay.rel(lay.claims)}")
    return path


def next_id(lay: Layout) -> str:
    nums = [int(_ID.match(p.name).group(1)) for p in lay.claims.glob("C*.md") if _ID.match(p.name)]
    return f"C{(max(nums) + 1 if nums else 1):02d}"


def _text(d: dict, key: str, what: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise InputError(f"{key}: required non-empty string ({what})")
    return v.strip()


def list_claims(lay: Layout) -> list[dict]:
    rows = []
    for path in sorted(lay.claims.glob("C*.md"), key=lambda p: int(_ID.match(p.name).group(1)) if _ID.match(p.name) else 0):
        if not _ID.match(path.name):
            continue
        fm, body = parse(path.read_text(encoding="utf-8"))
        fm["id"] = path.stem
        fm["body"] = body
        fm["path"] = path
        rows.append(fm)
    return rows


def by_status(lay: Layout) -> dict[str, list[str]]:
    out = {s: [] for s in STATUSES}
    for c in list_claims(lay):
        out.setdefault(str(c.get("claim_status")), []).append(c["id"])
    return out


def _check_evidence(ev, prefix="evidence") -> list[dict]:
    if not isinstance(ev, list):
        raise InputError(f"{prefix}: must be a list")
    out = []
    for i, e in enumerate(ev):
        if not isinstance(e, dict):
            raise InputError(f"{prefix}[{i}]: must be an object")
        if "registry" in e:
            if e.get("statistic") not in STATISTICS:
                raise InputError(f"{prefix}[{i}].statistic: one of {STATISTICS} required with a registry reference")
            out.append({"registry": str(e["registry"]), "statistic": e["statistic"]})
        elif "source" in e:
            if not str(e.get("locator") or "").strip():
                raise InputError(f"{prefix}[{i}].locator: required with a source reference (a section, table or page)")
            out.append({"source": str(e["source"]), "locator": str(e["locator"])})
        else:
            raise InputError(f"{prefix}[{i}]: needs `registry` + `statistic` or `source` + `locator`")
    return out


def resolve_evidence(lay: Layout, evidence: list[dict]) -> list[dict]:
    """One finding per evidence item that does not exist; empty means every item resolves."""
    findings = []
    registry = {}
    if lay.registry_json.exists():
        try:
            data = json.loads(lay.registry_json.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                raise TypeError("entries")
            registry = {str(e.get("id")): e for e in entries if isinstance(e, dict) and isinstance(e.get("statistics"), dict)}
        except (ValueError, KeyError, TypeError, AttributeError):
            findings.append({"severity": "major", "message": "registry.json is unreadable or not {entries: [...]} (rebuild it with `registry`)", "location": lay.rel(lay.registry_json)})
    for i, e in enumerate(evidence):
        if "registry" in e:
            entry = registry.get(e["registry"])
            if entry is None:
                findings.append({"severity": "major", "message": f"registry entry {e['registry']} does not exist (run `registry` after a sealed run)", "location": f"evidence[{i}]"})
            elif e["statistic"] not in (entry.get("statistics") or {}):
                findings.append({"severity": "major", "message": f"registry entry {e['registry']} has no statistic {e['statistic']!r}", "location": f"evidence[{i}]"})
        else:
            src = e["source"]
            path = (lay.root / src.lstrip("/")).resolve()
            sources = lay.papers_sources.resolve()
            inside = path.parent == sources and path.suffix == ".md" and path.is_file()
            if not inside:
                findings.append({"severity": "major", "message": f"source {src} is not a file directly under papers/sources/ (save it with /save-paper first)", "location": f"evidence[{i}]"})
    return findings


def add(lay: Layout, d: dict, *, kind: str, by: str, now: str | None = None) -> Path:
    if kind not in KINDS:
        raise InputError(f"kind: must be one of {KINDS}")
    if by not in AUTHORS:
        raise InputError(f"by: must be one of {AUTHORS}")
    if not isinstance(d, dict):
        raise InputError("claim must be a JSON object")
    title = _text(d, "title", "the claim as one English sentence")
    description = _text(d, "description", "one line: what would make this claim true or false")
    status = d.get("claim_status", "candidate")
    if status not in STATUSES:
        raise InputError(f"claim_status: must be one of {STATUSES}")
    evidence = _check_evidence(d.get("evidence") or [])
    path = lay.claims / f"{next_id(lay)}.md"
    fm = {
        "type": "Claim",
        "title": title,
        "description": description,
        "kind": kind,
        "by": by,
        "claim_status": status,
        "evidence": evidence,
        "prereg": d.get("prereg"),
        "created": now or now_iso(),
        "updated": None,
    }
    if status == "supported":
        findings = resolve_evidence(lay, evidence)
        if findings or not evidence:
            raise GateError("supported requires evidence that resolves", findings=findings or [{"severity": "major", "message": "no evidence", "location": "evidence"}])
    path.write_text(dump(fm, str(d.get("body") or "")), encoding="utf-8")
    write_readme(lay)
    return path


def update(lay: Layout, cid: str, patch: dict, *, now: str | None = None) -> Path:
    """Merge ``patch`` into the claim; omitted fields survive, ``by`` never changes, ``supported`` must resolve."""
    path = _find(lay, cid)
    fm, body = parse(path.read_text(encoding="utf-8"))
    if not isinstance(patch, dict):
        raise InputError("patch must be a JSON object")
    if "by" in patch and patch["by"] != fm.get("by"):
        raise InputError(f"by: immutable (is {fm.get('by')!r}); authorship is recorded once")
    if "kind" in patch and patch["kind"] not in KINDS:
        raise InputError(f"kind: must be one of {KINDS}")
    if "claim_status" in patch and patch["claim_status"] not in STATUSES:
        raise InputError(f"claim_status: must be one of {STATUSES}")
    new = dict(fm)
    for key in ("title", "description"):
        if key in patch:
            new[key] = _text(patch, key, "must stay a non-empty string")
    for key in ("kind", "claim_status", "prereg"):
        if key in patch:
            new[key] = patch[key]
    if "evidence" in patch:
        new["evidence"] = _check_evidence(patch["evidence"])
    if "body" in patch:
        body = str(patch["body"])
    if new.get("claim_status") == "supported":
        findings = resolve_evidence(lay, new.get("evidence") or [])
        if findings or not new.get("evidence"):
            raise GateError(f"{cid}: supported requires evidence that resolves", findings=findings or [{"severity": "major", "message": "no evidence", "location": "evidence"}])
    new["updated"] = now or now_iso()
    path.write_text(dump(new, body), encoding="utf-8")
    write_readme(lay)
    return path
