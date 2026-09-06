"""Subcommand registration for ``research.py``; every handler returns ``{status, paths, findings, ...}``."""

from __future__ import annotations

import json
import sys

from . import claims, decisions, project
from .doctor import doctor
from .status import status


def _root(args):
    return project.find_root(args.root)


def _stdin_json():
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        from .errors import InputError

        raise InputError(f"stdin is not JSON: {exc}")


def cmd_init(args):
    lay = project.init_project(_root(args), args.slug, question=args.question or "")
    return {"status": "ok", "paths": [lay.rel(lay.project_md), lay.rel(lay.readme)]}


def cmd_status(args):
    lay = project.Layout.open(_root(args), args.slug)
    return status(lay)


def cmd_decide_propose(args):
    lay = project.Layout.open(_root(args), args.slug)
    path = decisions.propose(lay, _stdin_json())
    return {"status": "proposed", "id": decisions.decision_id(path), "paths": [lay.rel(path)]}


def cmd_decide_resolve(args):
    lay = project.Layout.open(_root(args), args.slug)
    path = decisions.resolve(lay, args.id, chosen=args.chosen, dissent=args.dissent, by=args.by)
    return {"status": "resolved", "id": decisions.decision_id(path), "paths": [lay.rel(path)]}


def cmd_claim_add(args):
    lay = project.Layout.open(_root(args), args.slug)
    path = claims.add(lay, _stdin_json(), kind=args.kind, by=args.by)
    return {"status": "added", "id": path.stem, "paths": [lay.rel(path)]}


def cmd_claim_update(args):
    lay = project.Layout.open(_root(args), args.slug)
    path = claims.update(lay, args.id, _stdin_json())
    return {"status": "updated", "id": path.stem, "paths": [lay.rel(path)]}


def cmd_doctor(args):
    return doctor()


def register(sub):
    sp = sub.add_parser("init", help="create projects/<slug>/ with the contract tree; an existing project is left untouched", description="Create project.md, literature.md, the empty subdirectories and the generated README. Running it again on an existing project changes nothing.")
    sp.add_argument("slug", help="lowercase kebab-case project name; becomes projects/<slug>/")
    sp.add_argument("--question", help="the research question, stored in project.md as `question`")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status", help="the session's opening screen: phase, open decisions, claims by status, prereg drift, undispositioned findings, gate readiness, sycophancy dials", description="Read projects/<slug>/ and report everything a session needs before acting. The recommendation=chosen and asked ratios are measurements, not thresholds: a human's justified acceptance is in them too.")
    sp.add_argument("slug", help="project slug")
    sp.set_defaults(func=cmd_status)

    d = sub.add_parser("decide", help="the decision ledger: propose (options + recommendation first), then resolve (the choice, afterwards)", description="Two-step decisions. `propose` reads a JSON proposal on stdin and writes decisions/NNN-<slug>.md with the recommendation; `resolve` fills the choice later. The order is in the file, so a recommendation cannot be bent to an answer already heard.")
    dsub = d.add_subparsers(dest="decide_cmd", required=True, metavar="ACTION")
    sp = dsub.add_parser("propose", help="record title, asked, 2-4 options (label, fails_when, evidence or evidence_gap), recommendation from stdin JSON", description="stdin JSON: {title, asked: bool, options: [{label, fails_when, evidence: [refs], evidence_gap}], recommendation: label, supersedes: D0NN|null, body, slug}. A missing cell exits 2 and names its path (options[1].fails_when).")
    sp.add_argument("slug", help="project slug")
    sp.set_defaults(func=cmd_decide_propose)
    sp = dsub.add_parser("resolve", help="record the chosen option of an open decision", description="Fill chosen, resolved_at and decided_by of decisions/<id>. Refused when the decision is already resolved (propose a new one with supersedes) or the label is not one of its options.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("id", help="decision id, e.g. D001")
    sp.add_argument("--chosen", required=True, help="label of the chosen option")
    sp.add_argument("--dissent", help="Claude's grounds for disagreeing when the choice differs from the recommendation (Korean)")
    sp.add_argument("--by", choices=decisions.DECIDERS, help="who decided; default human:seongjin when asked was true, claude otherwise")
    sp.set_defaults(func=cmd_decide_resolve)

    c = sub.add_parser("claim", help="claims (English): add with kind and author, update fields; `supported` must resolve its evidence", description="Claims are the sentences that become the paper. `by` is recorded once and never changes; rejected claims are set to `dropped`, never deleted, so every review packet still sees them.")
    csub = c.add_subparsers(dest="claim_cmd", required=True, metavar="ACTION")
    sp = csub.add_parser("add", help="create claims/CNN.md from stdin JSON {title, description, evidence, prereg, claim_status, body}", description="stdin JSON: {title: one English sentence, description: one line (what makes it true or false), evidence: [{registry: <run/condition/metric>, statistic} | {source: /papers/sources/<id>.md, locator}], prereg: P0N|null, claim_status (default candidate), body}.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--kind", required=True, choices=claims.KINDS, help="what kind of statement this is")
    sp.add_argument("--by", required=True, choices=claims.AUTHORS, help="who authored it; immutable afterwards")
    sp.set_defaults(func=cmd_claim_add)
    sp = csub.add_parser("update", help="merge stdin JSON fields into claims/<id>.md (omitted fields survive; by is immutable)", description="Partial update. Setting claim_status to supported requires every evidence item to resolve against registry.json or papers/sources/ (exit 6 with one finding per item otherwise).")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("id", help="claim id, e.g. C01")
    sp.set_defaults(func=cmd_claim_update)

    sp = sub.add_parser("doctor", help="check tectonic, codex, claude and python deps; print install commands (exit 7 if a required one is missing)", description="Verify every external prerequisite of build, review and ideate.")
    sp.set_defaults(func=cmd_doctor)
