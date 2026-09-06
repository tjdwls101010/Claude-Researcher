"""Viva: sampled claims 성진 explains before submission; the record is bound to the draft hash. (Sampling and recording arrive in S4.)"""

from __future__ import annotations

from .project import Layout


def gate(lay: Layout, draft_hash: str) -> tuple[str | None, list[dict]]:
    return None, [{"severity": "major", "message": f"no viva record (`viva sample` then `viva record`) is bound to draft hash {draft_hash[:12]}", "location": "viva/"}]
