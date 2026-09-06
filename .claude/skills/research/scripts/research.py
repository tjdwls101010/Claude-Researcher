#!/usr/bin/env python3
"""Research ledgers, sealed experiment runs, registry, paper verification and bound reviews for projects/<slug>/.

Exit codes:
  0  ok
  2  input error (the message names the missing or malformed field by path)
  3  project, file or reference not found
  5  a subprocess failed (tectonic, codex, claude, the experiment); its own exit code is reported separately
  6  verification failed or a gate blocked (findings list each item with its location)
  7  a prerequisite tool is missing (doctor prints the install command)

Every command accepts --json and prints {"status", "paths", "findings", "error"}.
Project root: --root > $CLAUDE_PROJECT_DIR > cwd.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (_HERE, _HERE.parent.parent / "save-paper" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from research import __version__  # noqa: E402
from research.errors import EXIT_OK, ResearchError  # noqa: E402


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


class _Parser(argparse.ArgumentParser):
    """argparse errors go through the same envelope as every other failure when --json was asked for."""

    def error(self, message):
        argv = sys.argv[1:] if _CURRENT_ARGV is None else _CURRENT_ARGV
        if "--json" in argv:
            print(json.dumps({"status": "error", "paths": [], "findings": [], "error": f"{self.prog}: {message}"}, ensure_ascii=False))
            sys.exit(2)
        super().error(message)


_CURRENT_ARGV = None


def main(argv=None) -> int:
    global _CURRENT_ARGV
    _CURRENT_ARGV = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    argv = _CURRENT_ARGV
    tail = []
    if "--" in argv:  # everything after `--` is the experiment's own command line, never parsed here
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1:]
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    args.argv = tail
    try:
        result = args.func(args)
    except ResearchError as exc:
        result = {"status": "error", "paths": [], "findings": [f if isinstance(f, dict) else {"message": str(f)} for f in exc.findings], "error": str(exc)}
        result.update(exc.data)
        return emit(args, result, exc.exit_code)
    except KeyboardInterrupt:
        eprint("interrupted")
        return 130
    return emit(args, result, EXIT_OK)


def emit(args, result: dict, code: int) -> int:
    result.setdefault("status", "ok")
    result.setdefault("paths", [])
    result.setdefault("findings", [])
    result.setdefault("error", None)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        line = result["status"] if code == EXIT_OK else f"error ({code}): {result['error']}"
        print(line)
        for p in result["paths"]:
            print(f"  {p}")
        for f in result["findings"]:
            if isinstance(f, dict):
                loc = f.get("location") or f.get("path") or ""
                print(f"  - {f.get('severity', 'note')}: {f.get('message', f)}" + (f"  [{loc}]" if loc else ""))
            else:
                print(f"  - {f}")
        for c in result.get("checks") or []:
            print(f"  {'ok  ' if c['ok'] else ('FAIL' if c['required'] else 'warn')}  {c['name']:<16} {c['detail']}")
        for k, v in result.items():
            if k not in ("status", "paths", "findings", "error", "checks") and v not in (None, [], {}):
                print(f"  {k}: {json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v}")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="research.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version-info", action="version", version=f"research {__version__}", help="print the tool version and exit")
    p.add_argument("--root", help="project root containing projects/ and papers/ (default: $CLAUDE_PROJECT_DIR, else cwd)")
    p.add_argument("--json", action="store_true", help="print the result as JSON: {status, paths, findings, error}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", parser_class=_Parser)
    from research import cli  # noqa: E402

    cli.register(sub)
    _add_shared(p)
    return p


def _add_shared(parser):
    """--root and --json are accepted after any (sub)command too, so `status toy --json` works."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                child.add_argument("--root", dest="root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
                child.add_argument("--json", dest="json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
                _add_shared(child)


if __name__ == "__main__":
    sys.exit(main())
