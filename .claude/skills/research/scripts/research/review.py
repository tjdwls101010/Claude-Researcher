"""Reviews: two-stage adversarial review bound to a packet hash, and the disposition log. (Runner and packet assembly arrive in S4.)"""

from __future__ import annotations

from .project import Layout


def design_gate(lay: Layout, prereg_id: str) -> tuple[str | None, list[dict]]:
    return None, [{"severity": "major", "message": f"no design review (`review request --scope design`) covers {prereg_id} with every major finding dispositioned", "location": "reviews/"}]


def draft_gate(lay: Layout, draft_hash: str) -> tuple[str | None, list[dict]]:
    return None, [{"severity": "major", "message": f"no draft review (`review request --scope draft`) matches draft hash {draft_hash[:12]} with every major finding dispositioned", "location": "reviews/"}]
