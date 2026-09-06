"""Subcommand registration for ``research.py``; every handler returns ``{status, paths, findings, ...}``.

Module imports inside the handlers are deliberate: ``--help`` and ``doctor`` must
work on a machine without PyYAML, and the ledger modules pull it in.
"""

from __future__ import annotations

import json
import sys

from . import meta


def _root(args):
    from .project import find_root

    return find_root(getattr(args, "root", None))


def _open(args):
    from .project import Layout

    return Layout.open(_root(args), args.slug)


def _stdin_json():
    from .errors import InputError

    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise InputError(f"stdin is not JSON: {exc}")


def cmd_init(args):
    from .project import init_project

    lay = init_project(_root(args), args.slug, question=args.question or "")
    return {"status": "ok", "paths": [lay.rel(lay.project_md), lay.rel(lay.readme)]}


def cmd_status(args):
    from .status import status

    return status(_open(args))


def cmd_decide_propose(args):
    from . import decisions

    lay = _open(args)
    path = decisions.propose(lay, _stdin_json())
    return {"status": "proposed", "id": decisions.decision_id(path), "paths": [lay.rel(path)]}


def cmd_decide_resolve(args):
    from . import decisions

    lay = _open(args)
    path = decisions.resolve(lay, args.id, chosen=args.chosen, dissent=args.dissent, by=args.by)
    return {"status": "resolved", "id": decisions.decision_id(path), "paths": [lay.rel(path)]}


def cmd_claim_add(args):
    from . import claims

    lay = _open(args)
    path = claims.add(lay, _stdin_json(), kind=args.kind, by=args.by)
    return {"status": "added", "id": path.stem, "paths": [lay.rel(path)]}


def cmd_claim_update(args):
    from . import claims

    lay = _open(args)
    path = claims.update(lay, args.id, _stdin_json())
    return {"status": "updated", "id": path.stem, "paths": [lay.rel(path)]}


def cmd_prereg_freeze(args):
    from . import prereg
    from pathlib import Path

    lay = _open(args)
    path = prereg.freeze(lay, Path(args.analysis))
    return {"status": "frozen", "id": path.stem, "paths": [lay.rel(path)]}


def cmd_prereg_check(args):
    from . import prereg

    lay = _open(args)
    pid = prereg.require_clean(lay, args.prereg)
    return {"status": "clean", "id": pid}


def cmd_run_start(args):
    from pathlib import Path

    from . import runs

    lay = _open(args)
    seeds = _parse_seeds(args.seeds)
    return runs.start(lay, args.name, args.argv, seeds=seeds, confirmatory=args.confirmatory, prereg=args.prereg, cwd=Path(args.cwd) if args.cwd else None, timeout=args.timeout)


def cmd_run_import(args):
    from pathlib import Path

    from . import runs

    lay = _open(args)
    return runs.import_run(lay, Path(args.dir), Path(args.manifest), name=args.name, seeds=_parse_seeds(args.seeds), prereg=args.prereg)


def cmd_registry(args):
    from . import registry

    lay = _open(args)
    reg = registry.rebuild(lay, min_seeds=args.min_seeds, strict=args.strict)
    return {"status": "ok", "paths": [lay.rel(lay.registry_json)], "entries": len(reg["entries"]), "excluded_runs": reg["excluded_runs"], "findings": [{"severity": "warning", "message": w["message"], "location": w["run_id"]} for w in reg["warnings"]]}


def _parse_seeds(text: str) -> list[int]:
    from .errors import InputError

    try:
        return [int(s) for s in str(text).split(",") if s.strip()]
    except ValueError:
        raise InputError("--seeds: comma-separated integers, e.g. 1,2,3")


def cmd_paper_init(args):
    from pathlib import Path

    from . import paper

    lay = _open(args)
    return paper.init(lay, Path(args.template), main=args.main, source=args.source)


def cmd_paper_results(args):
    from . import paper

    lay = _open(args)
    return {"status": "ok", "paths": [lay.rel(paper.write_results(lay))]}


def cmd_paper_figures(args):
    from . import paper

    return paper.run_figures(_open(args))


def _result_files(lay, names):
    return [lay.paper / n for n in names] if names else None


def cmd_paper_verify(args):
    from . import verify

    lay = _open(args)
    return verify.verify_paper(lay, result_files=_result_files(lay, args.result_files))


def cmd_build(args):
    from . import paper

    lay = _open(args)
    return paper.build(lay, final=args.final, offline=args.offline, result_files=_result_files(lay, args.result_files))


def cmd_doctor(args):
    from .doctor import doctor

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
    sp.add_argument("--by", choices=meta.DECIDERS, help="who decided; default human:seongjin when asked was true, claude otherwise")
    sp.set_defaults(func=cmd_decide_resolve)

    c = sub.add_parser("claim", help="claims (English): add with kind and author, update fields; `supported` must resolve its evidence", description="Claims are the sentences that become the paper. `by` is recorded once and never changes; rejected claims are set to `dropped`, never deleted, so every review packet still sees them.")
    csub = c.add_subparsers(dest="claim_cmd", required=True, metavar="ACTION")
    sp = csub.add_parser("add", help="create claims/CNN.md from stdin JSON {title, description, evidence, prereg, claim_status, body}", description="stdin JSON: {title: one English sentence, description: one line (what makes it true or false), evidence: [{registry: <run/condition/metric>, statistic} | {source: /papers/sources/<id>.md, locator}], prereg: P0N|null, claim_status (default candidate), body}.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--kind", required=True, choices=meta.KINDS, help="what kind of statement this is")
    sp.add_argument("--by", required=True, choices=meta.AUTHORS, help="who authored it; immutable afterwards")
    sp.set_defaults(func=cmd_claim_add)
    sp = csub.add_parser("update", help="merge stdin JSON fields into claims/<id>.md (omitted fields survive; by is immutable)", description="Partial update. Setting claim_status to supported requires every evidence item to resolve against registry.json or papers/sources/ (exit 6 with one finding per item otherwise).")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("id", help="claim id, e.g. C01")
    sp.set_defaults(func=cmd_claim_update)

    pr = sub.add_parser("prereg", help="preregistration: freeze hypotheses/predictions + analysis plan with hashes; check drift since", description="HARKing prevention. `freeze` snapshots every hypothesis and prediction claim (content hash) and the analysis plan file before results are looked at; `check` reports what changed since (exit 6 on drift). A confirmatory run requires a clean check.")
    prsub = pr.add_subparsers(dest="prereg_cmd", required=True, metavar="ACTION")
    sp = prsub.add_parser("freeze", help="write prereg/PNN.md with claim bytes, plan bytes and sha256 of each; stamps `prereg` on those claims", description="Snapshot the current hypothesis/prediction claims (not dropped) and the analysis plan file. Nothing is looked at here; this is the timestamp results are judged against.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--analysis", required=True, help="path to the analysis plan file (tests, seeds, alpha, exclusion rules) to freeze")
    sp.set_defaults(func=cmd_prereg_freeze)
    sp = prsub.add_parser("check", help="report claims changed/added/removed and plan edits since a freeze; exit 6 on drift", description="Compare the current hypothesis/prediction claims and the plan file with a preregistration (default: the latest).")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--prereg", help="preregistration id (default: latest)")
    sp.set_defaults(func=cmd_prereg_check)

    r = sub.add_parser("run", help="experiment runs: start one locally (sealed on completion) or import one produced elsewhere against a manifest", description="Runs live under experiments/runs/<rNNN>/ and are written only by this tool. A completed run with a valid results.json is sealed (sha256 of every file); only sealed runs enter the registry.")
    rsub = r.add_subparsers(dest="run_cmd", required=True, metavar="ACTION")
    sp = rsub.add_parser("start", help="run `-- <argv>` once without a shell; RESEARCH_RUN_DIR, RESEARCH_SEEDS (JSON list) and RESEARCH_RUN_ID are injected", description="The experiment must write results.json into $RESEARCH_RUN_DIR ({schema_version: 1, metric_def, conditions, observations: [{condition, seed, metrics: {name: \"decimal string\"}}]}). stdout+stderr go to output.log. A non-zero exit, a missing or invalid results.json, or a timeout is recorded (failed/interrupted) and never sealed. --confirmatory requires --prereg and a design review covering it (exit 6 names what is missing); without it the run is exploratory and cannot be cited as confirmatory evidence.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--name", required=True, help="short run name recorded in run.json (letters, digits, . _ -)")
    sp.add_argument("--seeds", default="1,2,3,4,5", help="comma-separated integer seeds passed as RESEARCH_SEEDS (default 1,2,3,4,5; how many is the claim's call, recorded in a Decision when it differs)")
    sp.add_argument("--confirmatory", action="store_true", help="a preregistered, design-reviewed run whose numbers may support a claim; needs --prereg")
    sp.add_argument("--prereg", help="preregistration id the run tests (required with --confirmatory; recorded otherwise)")
    sp.add_argument("--cwd", help="working directory for the experiment (default: the current directory; recorded in run.json)")
    sp.add_argument("--timeout", type=float, help="seconds before the experiment is interrupted (default: none)")
    sp.epilog = "The experiment command follows `--`: run start <slug> --name N --seeds 1,2,3 -- python3 train.py --lr 1e-3"
    sp.set_defaults(func=cmd_run_start)
    sp = rsub.add_parser("import", help="publish a run directory produced elsewhere (cloud GPU) after every file matches a manifest", description="The manifest is {files: [{path, size, sha256}]} written on the producing machine. Symlinks, absolute or escaping paths, unlisted files and any size/hash mismatch refuse the import (exit 6); nothing is published on refusal.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("dir", help="directory holding results.json (plus output.log, artifacts/)")
    sp.add_argument("--manifest", required=True, help="path to the manifest JSON")
    sp.add_argument("--name", required=True, help="short run name")
    sp.add_argument("--seeds", required=True, help="comma-separated seeds the run was expected to cover")
    sp.add_argument("--prereg", help="preregistration id the run tests (recorded; the run is still exploratory)")
    sp.set_defaults(func=cmd_run_import)

    sp = sub.add_parser("registry", help="rebuild experiments/registry.json from sealed runs and report sanity warnings (exit 6 with --strict)", description="One entry per (run, condition, metric) with values and Decimal statistics. Warnings: identical per-seed values across conditions, identical means, too few seeds, non-finite values, missing metric_def. Resolve them before interpreting anything.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--strict", action="store_true", help="exit 6 when any warning remains")
    sp.add_argument("--min-seeds", type=int, help="seeds every entry needs (default: the run's expected_seeds)")
    sp.set_defaults(func=cmd_registry)

    pa = sub.add_parser("paper", help="the draft: init from a conference template, regenerate results.tex, run figure scripts, verify numbers/citations/figures", description="Numbers reach the PDF only as \\result{entry}{stat}{digits} (pre-rendered from the registry into results.tex); \\nonresult{literal}{reason} is the explicit exception for literature numbers. Figures are produced by paper/figures/<name>.py scripts and recorded in a manifest. verify refuses anything else with a file:line.")
    pasub = pa.add_subparsers(dest="paper_cmd", required=True, metavar="ACTION")
    sp = pasub.add_parser("init", help="copy a conference template into paper/template/ with source URL and file hashes; scaffold main.tex, refs.bib, results.tex", description="The template is never bundled: verify the current year's files first (ultra-search), download them, then point --template at that directory. An existing main.tex is kept.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--template", required=True, help="directory holding the downloaded template (style files and example main)")
    sp.add_argument("--main", default="main.tex", help="the template's main .tex file, copied to paper/main.tex when none exists (default main.tex)")
    sp.add_argument("--source", help="URL the template was downloaded from (recorded in provenance.json)")
    sp.set_defaults(func=cmd_paper_init)
    sp = pasub.add_parser("results", help="regenerate paper/results.tex from experiments/registry.json", description="One control sequence per (entry, statistic, digits 0-6), plus \\result, \\nonresult and \\resultclass. An unknown entry is a LaTeX error at build time.")
    sp.add_argument("slug", help="project slug")
    sp.set_defaults(func=cmd_paper_results)
    sp = pasub.add_parser("figures", help="run paper/figures/*.py (each reads $RESEARCH_REGISTRY, writes $RESEARCH_FIGURE_OUT) and write figures/manifest.json", description="Scripts get RESEARCH_REGISTRY, RESEARCH_FIGURE_OUT (<name>.pdf), RESEARCH_SCRIPTS (for `from research.registry import load`) and MPLBACKEND=pdf. A script that exits non-zero or writes nothing fails the command.")
    sp.add_argument("slug", help="project slug")
    sp.set_defaults(func=cmd_paper_figures)
    sp = pasub.add_parser("verify", help="numbers only via macros in result files, citations saved+verified (arXiv) or human-verified (literature.md), figures from the manifest, no [MATERIAL GAP]", description="Exit 6 with one finding per problem, each with paper/<file>:<line>. Exploratory entries cited in a result file are warnings.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--result-files", nargs="+", metavar="FILE", help="paths under paper/ whose numbers must all be macros (default: project.md result_files, else sections/results.tex and sections/experiments.tex when present)")
    sp.set_defaults(func=cmd_paper_verify)

    sp = sub.add_parser("build", help="paper results + figures + verify + tectonic → paper/build/main.pdf; --final adds the review and viva gates", description="--final refuses (exit 6, naming what is missing) unless a scope:draft review of this exact draft hash has every major finding dispositioned and a viva record is bound to the same hash. Any edit to the draft invalidates both.")
    sp.add_argument("slug", help="project slug")
    sp.add_argument("--final", action="store_true", help="the submission build: requires the draft review and the viva bound to the current draft hash")
    sp.add_argument("--result-files", nargs="+", metavar="FILE", help="as in `paper verify`")
    sp.add_argument("--offline", action="store_true", help="tectonic --only-cached: build from the local package cache without the network (fails if a package was never fetched)")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("doctor", help="check tectonic, codex, claude and python deps; print install commands (exit 7 if a required one is missing)", description="Verify every external prerequisite of build, review and ideate.")
    sp.set_defaults(func=cmd_doctor)
