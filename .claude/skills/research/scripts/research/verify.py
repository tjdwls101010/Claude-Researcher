"""Deterministic checks on the draft: numbers only through macros, citations only through verified sources, figures only from the manifest, no material gaps.

Each check returns findings ``{severity, message, location}`` with ``location`` as
``paper/<file>:<line>``; ``verify_paper`` composes them and raises exit 6 on any
major finding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from savepaper.frontmatter import parse

from . import registry as registry_mod
from .errors import GateError, NotFoundError
from .project import Layout

RESULT_RE = re.compile(r"\\result\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}")
NONRESULT_RE = re.compile(r"\\nonresult\{([^}]*)\}\{([^}]*)\}")
RESULTCLASS_RE = re.compile(r"\\resultclass\{([^}]*)\}")
NUMBER_RE = re.compile(r"(?<![A-Za-z\\@:._/-])[-+−]?\d+(?:[.,]\d+)*(?:\s?%|\\%)?(?![A-Za-z\\@_/-])")
# arguments of these macros are layout, labels or references, never results
_ARG_MACROS = ("multicolumn", "multirow", "cmidrule", "cline", "vspace", "hspace", "setlength", "addtolength", "includegraphics", "resizebox", "rule", "parbox", "minipage", "scalebox", "rotatebox", "arraystretch", "label", "ref", "eqref", "pageref", "cref", "Cref", "autoref", "cite", "citep", "citet", "citealp", "citealt", "citeauthor", "citeyear", "nocite", "input", "include", "bibliography", "bibliographystyle", "usepackage", "documentclass", "begin", "end", "url", "href", "hypersetup", "definecolor", "color", "textcolor", "footnotemark", "footnotetext", "caption", "newcommand", "renewcommand", "def", "linewidth", "textwidth", "columnwidth", "toprule", "midrule", "bottomrule", "hline", "newline", "noindent", "centering", "small", "footnotesize", "scriptsize", "tiny", "large")
_BRACED = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
_OPT = r"(?:\[[^\]]*\])*"
_PAREN = r"(?:\([^)]*\))?"
_ARG_RE = re.compile(r"\\(?:" + "|".join(_ARG_MACROS) + r")\*?" + _PAREN + _OPT + r"(?:\s*" + _BRACED + r")*")
_OPT_RE = re.compile(r"\\\\\[[^\]]*\]|\\[a-zA-Z]+\[[^\]]*\]")
CITE_RE = re.compile(r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|citeyearpar|nocite|parencite|textcite|autocite)\*?(?:\[[^\]]*\])*\{([^}]*)\}")
INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]*)\}")
GRAPHICS_RE = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^}]*)\}")
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
MATERIAL_GAP = "[MATERIAL GAP]"


def _strip_comments(line: str) -> str:
    out, i = [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "%":
            break
        out.append(c)
        i += 1
    return "".join(out)


def live_lines(path: Path):
    """(lineno, line) pairs LaTeX actually reads: comments stripped, nothing after \\end{document} or \\endinput."""
    for n, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = _strip_comments(raw)
        if "\\end{document}" in line or "\\endinput" in line:
            yield n, line.split("\\end{document}")[0].split("\\endinput")[0]
            return
        yield n, line


def _loc(lay: Layout, path: Path, lineno: int) -> str:
    return f"{lay.rel(path)}:{lineno}"


# --- numbers --------------------------------------------------------------------------------


def numbers(lay: Layout, result_files: list[Path]) -> list[dict]:
    """In result files every number must be a ``\\result`` or ``\\nonresult``; entries must exist with the statistic; exploratory entries warn."""
    reg = registry_mod.load(lay) if lay.registry_json.is_file() else {"entries": []}
    entries = {e["id"]: e for e in reg.get("entries", [])}
    findings = []
    for path in result_files:
        if not path.is_file():
            findings.append({"severity": "major", "message": "result file missing", "location": lay.rel(path)})
            continue
        for n, line in live_lines(path):
            for m in RESULT_RE.finditer(line):
                eid, stat, digits = m.group(1), m.group(2), m.group(3)
                e = entries.get(eid)
                if e is None:
                    findings.append({"severity": "major", "message": f"unknown registry entry {eid}: run `registry` (a sealed, completed run) before citing it", "location": _loc(lay, path, n)})
                    continue
                if (e.get("statistics") or {}).get(stat) is None:
                    findings.append({"severity": "major", "message": f"{eid} has no statistic {stat!r} (available: {sorted(k for k, v in (e.get('statistics') or {}).items() if v is not None)})", "location": _loc(lay, path, n)})
                if not digits.isdigit():
                    findings.append({"severity": "major", "message": f"digits must be an integer, got {digits!r}", "location": _loc(lay, path, n)})
                if e.get("class") == "exploratory":
                    findings.append({"severity": "warning", "message": f"{eid} is exploratory (no preregistration/design review) but is cited in a results file; it cannot support a claim", "location": _loc(lay, path, n)})
            scrubbed = RESULT_RE.sub(" ", line)
            scrubbed = NONRESULT_RE.sub(" ", scrubbed)
            scrubbed = RESULTCLASS_RE.sub(" ", scrubbed)
            scrubbed = _ARG_RE.sub(" ", scrubbed)
            scrubbed = _OPT_RE.sub(" ", scrubbed)
            scrubbed = re.sub(r"\$[^$]*\$", lambda mm: " " if not re.search(r"\d", mm.group(0)) else mm.group(0), scrubbed)
            for m in NUMBER_RE.finditer(scrubbed):
                findings.append({"severity": "major", "message": f"bare number {m.group(0).strip()!r}: write it as \\result{{entry}}{{stat}}{{digits}} or \\nonresult{{{m.group(0).strip()}}}{{source and locator}}", "location": _loc(lay, path, n)})
    return findings


# --- citations -------------------------------------------------------------------------------


def parse_bibtex(text: str) -> dict[str, dict]:
    """Structural BibTeX parse: {key: {field: value, _type}}; braces nest, quotes and @string names are values, trailing commas allowed."""
    entries: dict[str, dict] = {}
    strings: dict[str, str] = {}
    i = 0
    n = len(text)
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        j = at + 1
        while j < n and text[j].isalpha():
            j += 1
        etype = text[at + 1:j].lower()
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n or text[j] not in "{(":
            i = j
            continue
        close = "}" if text[j] == "{" else ")"
        depth, k = 1, j + 1
        while k < n and depth:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}" or (close == ")" and text[k] == ")" and depth == 1):
                depth -= 1
            k += 1
        body = text[j + 1:k - 1]
        i = k
        if etype in ("comment", "preamble"):
            continue
        fields = _parse_fields(body, strings, keyed=etype != "string")
        if etype == "string":
            strings.update({kk.lower(): v for kk, v in fields[1].items()})
            continue
        key, values = fields
        if key:
            values["_type"] = etype
            entries[key] = values
    return entries


def _parse_fields(body: str, strings: dict, *, keyed: bool) -> tuple[str | None, dict]:
    key = None
    rest = body
    if keyed:
        comma = body.find(",")
        if comma < 0:
            return body.strip() or None, {}
        key, rest = body[:comma].strip(), body[comma + 1:]
    fields: dict[str, str] = {}
    i, n = 0, len(rest)
    while i < n:
        eq = rest.find("=", i)
        if eq < 0:
            break
        name = rest[i:eq].strip().strip(",").strip().lower()
        j = eq + 1
        parts = []
        while True:
            while j < n and rest[j] in " \t\r\n":
                j += 1
            if j >= n:
                break
            c = rest[j]
            if c == "{":
                depth, k = 1, j + 1
                while k < n and depth:
                    depth += (rest[k] == "{") - (rest[k] == "}")
                    k += 1
                parts.append(rest[j + 1:k - 1])
                j = k
            elif c == '"':
                k = j + 1
                depth = 0
                while k < n and (rest[k] != '"' or depth):
                    depth += (rest[k] == "{") - (rest[k] == "}")
                    k += 1
                parts.append(rest[j + 1:k])
                j = k + 1
            else:
                k = j
                while k < n and rest[k] not in ",#\r\n}":
                    k += 1
                tok = rest[j:k].strip()
                parts.append(strings.get(tok.lower(), tok))
                j = k
            while j < n and rest[j] in " \t\r\n":
                j += 1
            if j < n and rest[j] == "#":
                j += 1
                continue
            break
        if name:
            fields[name] = "".join(parts).strip()
        while j < n and rest[j] in " \t\r\n,":
            j += 1
        i = j
    return key, fields


def tex_files(main: Path) -> list[Path]:
    """``main.tex`` and everything it ``\\input``s or ``\\include``s, recursively, in document order."""
    seen, order = set(), []

    def walk(p: Path):
        if p in seen or not p.is_file():
            return
        seen.add(p)
        order.append(p)
        for _, line in live_lines(p):
            for m in INPUT_RE.finditer(line):
                name = m.group(1).strip()
                child = main.parent / (name if name.endswith(".tex") else name + ".tex")
                walk(child)

    walk(main)
    return order


def _arxiv_of(entry: dict) -> tuple[str | None, int | None]:
    if str(entry.get("archiveprefix", "")).lower() == "arxiv" and entry.get("eprint"):
        m = re.match(r"\s*(\S+?)(v(\d+))?\s*$", entry["eprint"])
        if m:
            return m.group(1), int(m.group(3)) if m.group(3) else None
    for field in ("url", "doi", "journal", "note", "howpublished", "eprint", "booktitle"):
        m = ARXIV_RE.search(str(entry.get(field, "")))
        if m:
            return m.group(1), int(m.group(2)[1:]) if m.group(2) else None
    return None, None


def _literature(lay: Layout) -> dict[str, dict]:
    if not lay.literature_md.is_file():
        return {}
    fm, _ = parse(lay.literature_md.read_text(encoding="utf-8"))
    return {str(e.get("key")): e for e in (fm.get("entries") or []) if isinstance(e, dict) and e.get("key")}


def citations(lay: Layout, main: Path, bib: Path) -> list[dict]:
    """Every cited key must be in the bib; arXiv entries must be saved, verified sources at the same version; others must be human-verified in literature.md."""
    findings = []
    if not bib.is_file():
        return [{"severity": "major", "message": f"no bibliography at {lay.rel(bib)}", "location": lay.rel(bib), "key": None}]
    entries = parse_bibtex(bib.read_text(encoding="utf-8", errors="replace"))
    lit = _literature(lay)
    seen: set[str] = set()
    for path in tex_files(main):
        for n, line in live_lines(path):
            for m in CITE_RE.finditer(line):
                for key in (k.strip() for k in m.group(1).split(",")):
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    loc = _loc(lay, path, n)
                    e = entries.get(key)
                    if e is None:
                        findings.append({"severity": "major", "message": f"{key}: not in refs.bib", "location": loc, "key": key})
                        continue
                    aid, ver = _arxiv_of(e)
                    if aid:
                        findings.extend(_check_arxiv(lay, key, aid, ver, loc))
                    else:
                        le = lit.get(key)
                        v = (le or {}).get("verified") or {}
                        if not le or not str(v.get("by", "")).startswith("human:"):
                            findings.append({"severity": "major", "message": f"{key}: non-arXiv reference without a human-verified entry in literature.md (add `key: {key}` with `verified: {{by: human:seongjin, at: ...}}` after reading it)", "location": loc, "key": key})
    return findings


def _check_arxiv(lay: Layout, key: str, aid: str, ver: int | None, loc: str) -> list[dict]:
    safe = aid.replace("/", "_")
    src = lay.papers_sources / f"{safe}.md"
    if not src.is_file():
        return [{"severity": "major", "message": f"{key}: arXiv:{aid} is not saved under papers/sources/ (run /save-paper {aid})", "location": loc, "key": key}]
    fm, _ = parse(src.read_text(encoding="utf-8"))
    out = []
    saved_ver = (fm.get("arxiv") or {}).get("version")
    if ver is not None and saved_ver is not None and int(ver) != int(saved_ver):
        out.append({"severity": "major", "message": f"{key}: bib cites arXiv:{aid}v{ver} but the saved source is v{saved_ver}; align the bib or re-save with --version", "location": loc, "key": key})
    route = (fm.get("conversion") or {}).get("route")
    if not fm.get("verified"):
        sev = "warning" if route == "pdf" else "major"
        out.append({"severity": sev, "message": f"{key}: saved source {safe}.md is unverified (route: {route}); quote it only where the check says the text is present", "location": loc, "key": key})
    return out


# --- figures and gaps ------------------------------------------------------------------------


def figures(lay: Layout, files: list[Path]) -> list[dict]:
    man_path = lay.paper / "figures" / "manifest.json"
    known = set()
    if man_path.is_file():
        try:
            known = set(json.loads(man_path.read_text(encoding="utf-8")).get("figures", {}))
        except ValueError:
            pass
    findings = []
    for path in files:
        if not path.is_file():
            continue
        for n, line in live_lines(path):
            for m in GRAPHICS_RE.finditer(line):
                target = m.group(1).strip()
                name = Path(target).name
                if not (target.startswith("figures/") and name in known):
                    findings.append({"severity": "major", "message": f"\\includegraphics{{{target}}} is not in figures/manifest.json: every figure comes from a paper/figures/<name>.py script run by `paper figures`", "location": _loc(lay, path, n)})
    return findings


def material_gaps(files: list[Path], lay: Layout | None = None) -> list[dict]:
    findings = []
    for path in files:
        if not path.is_file():
            continue
        for n, raw in live_lines(path):
            if MATERIAL_GAP in raw:
                loc = f"{lay.rel(path)}:{n}" if lay else f"{path}:{n}"
                findings.append({"severity": "major", "message": f"{MATERIAL_GAP} left in the draft", "location": loc})
    return findings


def default_result_files(lay: Layout) -> list[Path]:
    fm, _ = lay.read_project()
    names = fm.get("result_files") or ["sections/results.tex", "sections/experiments.tex"]
    return [lay.paper / n for n in names if (lay.paper / n).is_file()]


def verify_paper(lay: Layout, *, result_files: list[Path] | None = None) -> dict:
    main = lay.paper / "main.tex"
    if not main.is_file():
        raise NotFoundError(f"no {lay.rel(main)}")
    files = tex_files(main)
    rfiles = list(result_files) if result_files else default_result_files(lay)
    findings = []
    findings += numbers(lay, rfiles)
    findings += citations(lay, main, lay.paper / "refs.bib")
    findings += figures(lay, files)
    findings += material_gaps(files, lay)
    majors = [f for f in findings if f["severity"] == "major"]
    if majors:
        raise GateError(f"paper verify: {len(majors)} problem(s), {len(findings) - len(majors)} warning(s)", findings=findings)
    return {"status": "verified", "findings": findings, "result_files": [lay.rel(p) for p in rfiles], "tex_files": [lay.rel(p) for p in files]}
