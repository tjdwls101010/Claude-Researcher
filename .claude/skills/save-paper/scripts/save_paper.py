#!/usr/bin/env python3
"""Save arXiv papers into the project knowledge base as faithful Markdown sources.

One `save` is a transaction: resolve the reference to an exact version, fetch the
arXiv HTML (LaTeXML) and the e-print for figure originals, convert with pandoc,
check that every paragraph/cell/caption/reference of the original is present,
then publish atomically under papers/sources/ and regenerate papers/README.md.
Papers with no HTML fall back to the PDF (text and tables only; unverified).

Exit codes:
  0  ok (saved and verified, or already up to date)
  2  usage error
  3  reference could not be resolved, or a title search is ambiguous (candidates printed as JSON)
  4  fetch failed (network, non-200 from arXiv)
  5  conversion failed (pandoc, anydoc, no LaTeXML article)
  6  saved but NOT verified: coverage check failed or PDF route; read `conversion.known_losses`
  7  prerequisites missing (doctor), or `describe <id>` run without OPENROUTER_API_KEY
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savepaper import __version__  # noqa: E402
from savepaper.arxiv import ArxivClient, resolve, safe_id  # noqa: E402
from savepaper.errors import EXIT_DOCTOR, EXIT_OK, EXIT_UNVERIFIED, EXIT_USAGE, AmbiguousRef, SavePaperError  # noqa: E402
from savepaper.publish import Layout  # noqa: E402

ROUTES = ("auto", "html", "pdf")


def project_root() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def default_out() -> str:
    return str(project_root() / "papers")


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


# --- commands -----------------------------------------------------------------


def cmd_save(args) -> int:
    from savepaper.describe import load_api_key
    from savepaper.pipeline import save_one

    layout = Layout(Path(args.out))
    describe = not args.no_describe
    api_key = load_api_key(project_root() / ".env") if describe else None
    try:
        out = save_one(
            args.ref,
            layout,
            ArxivClient(),
            route=args.route,
            version=args.version,
            force=args.force,
            with_assets=not args.no_assets,
            describe=describe,
            describe_model=args.model,
            api_key=api_key,
        )
    except AmbiguousRef as exc:
        print(json.dumps({"error": str(exc), "candidates": [c.as_dict() for c in exc.candidates]}, ensure_ascii=False, indent=2))
        return exc.exit_code
    print(out.summary())
    return out.exit


def cmd_batch(args) -> int:
    from savepaper.pipeline import save_one

    from savepaper.describe import load_api_key

    layout = Layout(Path(args.out))
    client = ArxivClient()
    describe = not args.no_describe
    api_key = load_api_key(project_root() / ".env") if describe else None
    refs = [l.strip() for l in Path(args.ids_file).read_text(encoding="utf-8").splitlines()]
    refs = [r for r in refs if r and not r.startswith("#")]
    worst = EXIT_OK
    rows = []
    for i, ref in enumerate(refs, start=1):
        eprint(f"[{i}/{len(refs)}] {ref}")
        try:
            out = save_one(ref, layout, client, route=args.route, force=args.force, with_assets=not args.no_assets, describe=describe, api_key=api_key)
            row = out.as_dict()
        except SavePaperError as exc:
            row = {"id": ref, "exit": exc.exit_code, "status": "failed", "error": str(exc)}
            if isinstance(exc, AmbiguousRef):
                row["candidates"] = [c.as_dict() for c in exc.candidates]
            eprint(f"  FAILED ({exc.exit_code}): {exc}")
        except Exception as exc:  # keep the batch going; the row records what happened
            row = {"id": ref, "exit": 1, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            eprint(f"  FAILED (1): {exc}")
        worst = max(worst, int(row.get("exit", 1)))
        rows.append(row)
        if args.jsonl:
            with open(args.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    ok = sum(1 for r in rows if r.get("exit") == 0)
    unverified = sum(1 for r in rows if r.get("exit") == EXIT_UNVERIFIED)
    failed = len(rows) - ok - unverified
    print(f"batch: {len(rows)} refs  ok={ok}  unverified={unverified}  failed={failed}")
    return worst


def cmd_describe(args) -> int:
    from savepaper.describe import describe_markdown, load_api_key

    layout = Layout(Path(args.out))
    md = layout.source_md(safe_id(args.id))
    if not md.exists():
        eprint(f"no saved source at {md}; run `save {args.id}` first")
        return EXIT_USAGE
    api_key = load_api_key(project_root() / ".env")
    if not api_key:
        eprint("OPENROUTER_API_KEY not set. Put it in the environment, or copy .claude/skills/save-paper/scripts/savepaper/.env.example to .env beside it and fill it in.")
        return EXIT_DOCTOR
    stats = describe_markdown(md, api_key, model=args.model, only_missing=not args.all, log=eprint)
    print(json.dumps({"path": str(md), "model": stats.model, "described": stats.count, "failed": stats.failed, "skipped": stats.skipped, "usage": stats.usage, "failures": stats.failures}, ensure_ascii=False, indent=2))
    return EXIT_OK if stats.failed == 0 else EXIT_UNVERIFIED


def cmd_resolve(args) -> int:
    try:
        r = resolve(args.ref, ArxivClient(), version=args.version)
    except AmbiguousRef as exc:
        print(json.dumps({"ambiguous": True, "query": exc.query, "candidates": [c.as_dict() for c in exc.candidates]}, ensure_ascii=False, indent=2))
        return exc.exit_code
    print(json.dumps({"id": r.id, "version": r.version, "vid": r.vid, "meta": r.meta.as_dict()}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_check(args) -> int:
    from savepaper.check import check
    from savepaper.frontmatter import parse

    layout = Layout(Path(args.out))
    md = layout.source_md(safe_id(args.id))
    if not md.exists():
        eprint(f"no saved source at {md}")
        return EXIT_USAGE
    fm, body = parse(md.read_text(encoding="utf-8"))
    conv = fm.get("conversion") or {}
    if conv.get("route") != "html":
        print(json.dumps({"path": str(md), "route": conv.get("route"), "checkable": False, "known_losses": conv.get("known_losses")}, indent=2))
        return EXIT_UNVERIFIED
    arx = fm.get("arxiv") or {}
    resp = ArxivClient().fetch_html(arx["id"], int(arx["version"]))
    if resp.status_code != 200:
        eprint(f"HTML fetch returned {resp.status_code}; cannot re-check")
        return 4
    report = check(resp.content.decode("utf-8", "replace"), body)
    print(json.dumps({"path": str(md), **report.as_dict()}, ensure_ascii=False, indent=2))
    return EXIT_OK if report.passed else EXIT_UNVERIFIED


def cmd_index(args) -> int:
    from savepaper.index import write_index

    path = write_index(Path(args.out))
    print(f"wrote {path}")
    return EXIT_OK


def cmd_note_check(args) -> int:
    from savepaper.notecheck import note_check

    report = note_check(Path(args.source), Path(args.note))
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return EXIT_OK if report.passed else EXIT_UNVERIFIED


def cmd_doctor(args) -> int:
    checks = []

    def tool(name, install, required=True):
        path = shutil.which(name)
        checks.append({"name": name, "ok": path is not None, "detail": path or f"missing -> {install}", "required": required})

    tool("pandoc", "brew install pandoc")
    tool("pdftoppm", "brew install poppler")
    tool("npx", "brew install node   (only the PDF fallback needs it: npx -y @firecrawl/anydoc)", required=False)
    for mod, pip in (("bs4", "beautifulsoup4"), ("lxml", "lxml"), ("requests", "requests"), ("yaml", "pyyaml")):
        try:
            importlib.import_module(mod)
            checks.append({"name": f"python:{mod}", "ok": True, "detail": "importable", "required": True})
        except ImportError:
            checks.append({"name": f"python:{mod}", "ok": False, "detail": f"missing -> python3 -m pip install {pip}", "required": True})
    from savepaper.describe import load_api_key

    key = load_api_key(project_root() / ".env")
    checks.append({"name": "OPENROUTER_API_KEY", "ok": bool(key), "detail": "set" if key else "not set -> figure alt text will be skipped (copy scripts/savepaper/.env.example to scripts/savepaper/.env)", "required": False})
    width = max(len(c["name"]) for c in checks)
    for c in checks:
        mark = "ok  " if c["ok"] else ("FAIL" if c["required"] else "warn")
        print(f"{mark}  {c['name']:<{width}}  {c['detail']}")
    missing = [c for c in checks if not c["ok"] and c["required"]]
    print(f"save-paper {__version__}: {'ready' if not missing else str(len(missing)) + ' required item(s) missing'}")
    return EXIT_OK if not missing else EXIT_DOCTOR


# --- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="save_paper.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version-info", action="version", version=f"save-paper {__version__}", help="print the tool version and exit")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add_out(sp):
        sp.add_argument("--out", default=default_out(), help="papers directory to write into (default: $CLAUDE_PROJECT_DIR/papers or ./papers)")

    sp = sub.add_parser("save", help="save one paper as papers/sources/<id>.md (+ figures) and refresh the index", description="Save one arXiv paper. <ref> may be an arXiv URL (abs/pdf/html, with or without vN), a bare id (new or legacy style), or a title; an ambiguous title prints candidates and exits 3 -- it is never auto-picked.")
    sp.add_argument("ref", help="arXiv URL, arXiv id (e.g. 2503.17523, 2503.17523v2, hep-th/9901001) or paper title")
    sp.add_argument("--route", choices=ROUTES, default="auto", help="html: arXiv HTML via LaTeXML adapter + pandoc (checked); pdf: anydoc text-only fallback (unverified); auto: html, then pdf if arXiv has no HTML (default)")
    sp.add_argument("--version", type=int, default=None, dest="version", help="pin an arXiv version number instead of the latest (also overrides a vN in the ref)")
    sp.add_argument("--force", action="store_true", help="re-convert even when the fingerprint says the saved file is up to date or a newer version exists; never skips the check")
    sp.add_argument("--no-assets", action="store_true", help="skip the e-print download and figure rendering; image links point at arxiv.org instead of local PNGs")
    sp.add_argument("--no-describe", action="store_true", help="skip figure alt text. By default every figure is described by an OpenRouter vision model (costs money, ~$0.03-0.10 per figure); without OPENROUTER_API_KEY the step is skipped with a warning and the alts stay empty")
    sp.add_argument("--model", default=None, help="OpenRouter model for alt text (default: OPENROUTER_MODEL from .env, else openai/gpt-5.6-luna)")
    add_out(sp)
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser("batch", help="save many refs from a file, continuing past failures", description="Save every ref listed in a file (one per line, # comments allowed). One failure does not stop the batch; the exit code is the worst one seen. Alt text is generated for every paper unless --no-describe.")
    sp.add_argument("--ids-file", required=True, help="text file with one arXiv ref per line")
    sp.add_argument("--route", choices=ROUTES, default="auto", help="conversion route for every ref (see `save --help`)")
    sp.add_argument("--force", action="store_true", help="re-convert papers that are already up to date")
    sp.add_argument("--no-assets", action="store_true", help="skip figure download/rendering for every ref")
    sp.add_argument("--no-describe", action="store_true", help="skip figure alt text for every ref (see `save --help`)")
    sp.add_argument("--jsonl", default=None, help="append one JSON line per ref here: {id, version, route, coverage, verified, exit, path, losses, ...}")
    add_out(sp)
    sp.set_defaults(func=cmd_batch)

    sp = sub.add_parser("describe", help="fill figure alt text of an already saved source with an OpenRouter vision model", description="Write a text replacement for each figure into the alt slot of ![alt](path) in papers/sources/<id>.md. Needs OPENROUTER_API_KEY (environment or .env); exits 7 without it.")
    sp.add_argument("id", help="arXiv id of a saved source (legacy ids with / are accepted)")
    sp.add_argument("--model", default=None, help="OpenRouter model id (default: OPENROUTER_MODEL from .env, else openai/gpt-5.6-luna)")
    sp.add_argument("--all", action="store_true", help="re-describe every figure, not only those with an empty alt")
    add_out(sp)
    sp.set_defaults(func=cmd_describe)

    sp = sub.add_parser("resolve", help="show what a ref resolves to (id, version, metadata) without saving", description="Resolve a URL, id or title against the arXiv API and print JSON. An ambiguous title prints the candidates (exact_title marks a case-insensitive exact match) and exits 3.")
    sp.add_argument("ref", help="arXiv URL, id or title")
    sp.add_argument("--version", type=int, default=None, dest="version", help="version number to report instead of the latest")
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("check", help="re-run the coverage check on a saved source against arXiv's current HTML", description="Fetch the HTML for the saved version again and report which original blocks are missing from the Markdown. PDF-route sources are not checkable (exit 6).")
    sp.add_argument("id", help="arXiv id of a saved source")
    add_out(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("index", help="regenerate papers/README.md from frontmatter", description="Rebuild the index from the frontmatter of papers/*.md and papers/sources/*.md. The README is a generated file; hand edits are overwritten.")
    add_out(sp)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("note-check", help="list result numbers and equations of a source that a Korean note does not contain", description="Compare a source Markdown with its restructured note: numbers with decimals/percent/thousands separators and display equations in the source body must appear verbatim in the note. Also checks the note's frontmatter and title shape. Exit 6 on structural problems.")
    sp.add_argument("--source", required=True, help="path to papers/sources/<id>.md")
    sp.add_argument("--note", required=True, help="path to papers/<id>.md")
    sp.set_defaults(func=cmd_note_check)

    sp = sub.add_parser("doctor", help="check pandoc, poppler, node/anydoc, python deps and the OpenRouter key; print install commands", description="Verify every external prerequisite and print the install command for anything missing. Exit 7 when a required item is missing.")
    sp.set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SavePaperError as exc:
        eprint(f"error ({exc.exit_code}): {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        eprint("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
