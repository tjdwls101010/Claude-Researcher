"""Frontmatter for the source layer (OKF conventions) and the fingerprint that makes ``save`` idempotent."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Optional

import yaml

from . import __version__
from .arxiv import Meta, Resolved

GENERATOR = f"save-paper/{__version__}"
VERIFIER = "process:save-paper-check"

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(arxiv_id: str, version: int, source_sha256: str, converter: str, options: dict) -> str:
    """Stable hash of everything that determines the output; equal fingerprints mean ``save`` is a no-op."""
    payload = json.dumps(
        {"id": arxiv_id, "version": version, "sha": source_sha256, "converter": converter, "options": options},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _date(iso: str) -> str:
    return iso[:10] if iso else ""


def build_source_frontmatter(
    resolved: Resolved,
    sources: list[dict],
    conversion: dict,
    verified: bool,
    generated_at: Optional[str] = None,
    figures_described: Optional[dict] = None,
) -> dict:
    m: Meta = resolved.meta
    arxiv_block = {
        "id": resolved.id,
        "version": resolved.version,
        "published": _date(m.published),
        "updated": _date(m.updated),
        "categories": list(m.categories),
    }
    if m.doi:
        arxiv_block["doi"] = m.doi
    if m.comment:
        arxiv_block["comment"] = m.comment
    if m.journal_ref:
        arxiv_block["journal_ref"] = m.journal_ref
    fm: dict = {
        "type": "Paper",
        "title": m.title,
        "description": m.summary,
        "resource": f"https://arxiv.org/abs/{resolved.vid}",
        "arxiv": arxiv_block,
        "authors": list(m.authors),
        "tags": list(m.categories),
        "sources": sources,
        "generated": {"by": GENERATOR, "at": generated_at or now_iso()},
    }
    if verified:
        fm["verified"] = {"by": VERIFIER, "at": generated_at or now_iso()}
    fm["conversion"] = conversion
    if figures_described:
        fm["figures_described"] = figures_described
    return fm


def dump(fm: dict, body: str) -> str:
    text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=float("inf"), default_flow_style=None)
    return f"---\n{text}---\n\n{body.lstrip()}"


def parse(text: str) -> tuple[dict, str]:
    """Split a Markdown file into (frontmatter dict, body). No frontmatter -> ({}, text)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    data = yaml.safe_load(m.group(1)) or {}
    return data, m.group(2).lstrip("\n")


def update(text: str, **fields) -> str:
    """Rewrite ``text`` with top-level frontmatter keys replaced or added; body untouched."""
    fm, body = parse(text)
    fm.update(fields)
    return dump(fm, body)
