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

RESULT_RE = re.compile(r"\\result\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}")
NONRESULT_RE = re.compile(r"\\nonresult\s*\{([^{}]*)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}")  # the reason may hold a \cite{key}
RESULTCLASS_RE = re.compile(r"\\resultclass\s*\{([^}]*)\}")
# a number that renders: not glued to a preceding letter/underscore (x_1, H100, GPT-4 are identifiers); a trailing
# unit or letter still counts (5ms, 10k) except ordinals (2nd)
NUMBER_RE = re.compile(r"(?<![A-Za-z_\\@\d.,-])[-+−]?\d+(?:[.,]\d+)*(?:\s?%|\\%|[A-Za-z]+)?")
_ORDINAL = re.compile(r"^\d+(?:st|nd|rd|th)$")
# structural arguments (never rendered): how many braced groups to drop after the macro's optional arguments
_STRUCT_ARGS = {
    "multicolumn": 2, "multirow": 2, "cmidrule": 1, "cline": 1, "vspace": 1, "hspace": 1, "setlength": 2, "addtolength": 2,
    "includegraphics": 1, "resizebox": 2, "rule": 2, "parbox": 1, "minipage": 1, "scalebox": 1, "rotatebox": 1,
    "label": 1, "ref": 1, "eqref": 1, "pageref": 1, "cref": 1, "Cref": 1, "autoref": 1, "cite": 1, "citep": 1, "citet": 1,
    "citealp": 1, "citealt": 1, "citeauthor": 1, "citeyear": 1, "citeyearpar": 1, "citetalias": 1, "citepalias": 1, "nocite": 1,
    "parencite": 1, "textcite": 1, "autocite": 1, "parencites": 2, "cites": 2, "input": 1, "include": 1, "bibliography": 1,
    "bibliographystyle": 1, "usepackage": 1, "documentclass": 1, "begin": 1, "end": 1, "url": 1, "href": 1, "hypersetup": 1,
    "definecolor": 3, "color": 1, "textcolor": 1, "arraystretch": 1, "renewcommand": 1, "newcommand": 1, "def": 0,
    "columnwidth": 0, "linewidth": 0, "textwidth": 0, "toprule": 0, "midrule": 0, "bottomrule": 0, "hline": 0,
}
_BRACED = r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}"
_OPT = r"(?:\s*\[[^\]]*\])*"
_PAREN = r"(?:\([^)]*\))?"
_OPT_RE = re.compile(r"\\\\\s*\[[^\]]*\]|\\[a-zA-Z]+\*?\s*\[[^\]]*\]")
_MACRO_RE = re.compile(r"\\([a-zA-Z]+)\*?")
CITE_RE = re.compile(r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|citeyearpar|citetalias|citepalias|nocite|parencite|textcite|autocite|parencites|cites)\*?(?:\s*\[[^\]]*\])*((?:\s*\{[^}]*\})+)")
INPUT_RE = re.compile(r"\\(?:input|include)\s*(?:\{([^}]*)\}|([^\s{}\\]+))")
GRAPHICS_RE = re.compile(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^}]*)\}")
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arXiv:)\s*(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
_DECISION_REF = re.compile(r"\bD\d{3}\b")
_SOURCE_REF = re.compile(r"papers/sources/[^\s)]+\.md")
_CITE_IN_REASON = re.compile(r"\\cite[a-z]*\*?\s*\{([^}]*)\}")


def _scrub_structural(text: str) -> str:
    """Drop the structural braced arguments of known macros; what remains is what the reader sees."""
    out, i = [], 0
    while True:
        m = _MACRO_RE.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        name = m.group(1)
        j = m.end()
        n = _STRUCT_ARGS.get(name)
        if n is None:
            out.append(" ")
            i = j
            continue
        pm = re.compile(_PAREN + _OPT).match(text, j)
        if pm:
            j = pm.end()
        for _ in range(n):
            bm = re.compile(r"\s*" + _BRACED).match(text, j)
            if not bm:
                break
            j = bm.end()
        out.append(" ")
        i = j
    return "".join(out)


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
        if re.match(r"\s*\\(?:end\{document\}|endinput)", line):
            return
        yield n, line


def _loc(lay: Layout, path: Path, lineno: int) -> str:
    return f"{lay.rel(path)}:{lineno}"


# --- numbers --------------------------------------------------------------------------------


def _reason_ok(reason: str, bib_keys: set[str]) -> bool:
    if not reason.strip():
        return False
    if _SOURCE_REF.search(reason) or _DECISION_REF.search(reason):
        return True
    keys = {k.strip() for m in _CITE_IN_REASON.finditer(reason) for k in m.group(1).split(",")}
    return bool(keys & bib_keys)


def numbers(lay: Layout, result_files: list[Path]) -> list[dict]:
    """In result files every number must be a ``\\result`` or a ``\\nonresult`` whose reason names a bib key, a saved source or a decision."""
    reg = registry_mod.load(lay) if lay.registry_json.is_file() else {"entries": []}
    entries = {e["id"]: e for e in reg.get("entries", [])}
    bib = lay.paper / "refs.bib"
    bib_keys = set(parse_bibtex(bib.read_text(encoding="utf-8", errors="replace"))) if bib.is_file() else set()
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
            for m in NONRESULT_RE.finditer(line):
                if not _reason_ok(m.group(2), bib_keys):
                    findings.append({"severity": "major", "message": f"\\nonresult{{{m.group(1)}}}: the reason must name where the number comes from: a \\cite key in refs.bib, a papers/sources/<id>.md path, or a decision id (D0NN)", "location": _loc(lay, path, n)})
            scrubbed = RESULT_RE.sub(" ", line)
            scrubbed = NONRESULT_RE.sub(" ", scrubbed)
            scrubbed = RESULTCLASS_RE.sub(" ", scrubbed)
            scrubbed = _OPT_RE.sub(" ", scrubbed)
            scrubbed = _scrub_structural(scrubbed)
            for m in NUMBER_RE.finditer(scrubbed):
                tok = m.group(0).strip()
                if _ORDINAL.match(tok):
                    continue
                findings.append({"severity": "major", "message": f"bare number '{tok}': write it as \\result{{entry}}{{stat}}{{digits}} or \\nonresult{{{tok}}}{{source and locator}}", "location": _loc(lay, path, n)})
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
        depth, k, quoted = 1, j + 1, False
        while k < n and depth:
            c = text[k]
            if c == '"' and depth == 1:
                quoted = not quoted
            elif c == "{":
                depth += 1
            elif c == "}" or (close == ")" and c == ")" and depth == 1 and not quoted):
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


def live_text(path: Path) -> tuple[str, list[int]]:
    """The file as one string of live lines plus a map from character offset to line number."""
    parts, starts, pos = [], [], 0
    for n, line in live_lines(path):
        parts.append(line + "\n")
        starts.append((pos, n))
        pos += len(line) + 1
    return "".join(parts), starts


def _line_of(starts: list[tuple[int, int]], offset: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid][0] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return starts[lo][1] if starts else 1


def tex_files(main: Path, *, outside: list[tuple[Path, str, int]] | None = None) -> list[Path]:
    """``main.tex`` and everything it ``\\input``s or ``\\include``s, recursively, in document order; inputs outside paper/ are reported via ``outside``."""
    seen, order = set(), []
    root = main.parent.resolve()

    def walk(p: Path):
        if p in seen or not p.is_file():
            return
        seen.add(p)
        order.append(p)
        text, starts = live_text(p)
        for m in INPUT_RE.finditer(text):
            name = (m.group(1) or m.group(2) or "").strip()
            child = main.parent / (name if name.endswith(".tex") else name + ".tex")
            try:
                child.resolve().relative_to(root)
            except ValueError:
                if outside is not None:
                    outside.append((p, name, _line_of(starts, m.start())))
                continue
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
        text, starts = live_text(path)
        for m in CITE_RE.finditer(text):
            n = _line_of(starts, m.start())
            keys = [k.strip() for grp in re.findall(r"\{([^}]*)\}", m.group(1)) for k in grp.split(",")]
            for key in keys:
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
    """Every ``\\includegraphics`` must name ``figures/<name>`` that the manifest lists, that exists, and whose bytes still match the manifest."""
    import hashlib

    man_path = lay.paper / "figures" / "manifest.json"
    known: dict = {}
    if man_path.is_file():
        try:
            known = json.loads(man_path.read_text(encoding="utf-8")).get("figures", {})
        except ValueError:
            pass
    figs = (lay.paper / "figures").resolve()
    findings = []
    for path in files:
        if not path.is_file():
            continue
        text, starts = live_text(path)
        for m in GRAPHICS_RE.finditer(text):
            n = _line_of(starts, m.start())
            target = m.group(1).strip()
            loc = _loc(lay, path, n)
            resolved = (lay.paper / target).resolve()
            if not target.startswith("figures/") or resolved.parent != figs or resolved.name not in known:
                findings.append({"severity": "major", "message": f"\\includegraphics{{{target}}} is not figures/<name> listed in figures/manifest.json: every figure comes from a paper/figures/<name>.py script run by `paper figures`", "location": loc})
                continue
            if not resolved.is_file():
                findings.append({"severity": "major", "message": f"{target} is in the manifest but the file is missing (run `paper figures`)", "location": loc})
                continue
            want = (known.get(resolved.name) or {}).get("output_sha256")
            if want and hashlib.sha256(resolved.read_bytes()).hexdigest() != want:
                findings.append({"severity": "major", "message": f"{target} differs from what its script produced (manifest hash mismatch): run `paper figures` instead of editing the file", "location": loc})
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


def default_result_files(lay: Layout) -> tuple[list[Path], bool]:
    """(files, configured): configured names are returned even when missing so the mistake is reported."""
    fm, _ = lay.read_project()
    names = fm.get("result_files")
    if names:
        return [lay.paper / n for n in names], True
    return [lay.paper / n for n in ("sections/results.tex", "sections/experiments.tex") if (lay.paper / n).is_file()], False


def verify_paper(lay: Layout, *, result_files: list[Path] | None = None) -> dict:
    from . import paper as paper_mod

    main = lay.paper / "main.tex"
    if not main.is_file():
        raise NotFoundError(f"no {lay.rel(main)}")
    outside: list = []
    files = tex_files(main, outside=outside)
    findings = []
    for src, name, n in outside:
        findings.append({"severity": "major", "message": f"\\input{{{name}}} reaches outside paper/; the draft hash and the review cannot bind it", "location": _loc(lay, src, n)})
    if result_files:
        rfiles = list(result_files)
    else:
        rfiles, _configured = default_result_files(lay)
    if not rfiles:
        findings.append({"severity": "warning", "message": "no result file is checked for bare numbers: set `result_files` in project.md or pass --result-files (default: sections/results.tex, sections/experiments.tex)", "location": lay.rel(lay.project_md)})
    findings += numbers(lay, rfiles)
    findings += citations(lay, main, lay.paper / "refs.bib")
    findings += figures(lay, files)
    findings += material_gaps(files, lay)
    results_tex = lay.paper / "results.tex"
    if results_tex.is_file() and results_tex.read_text(encoding="utf-8") != paper_mod.render_results(lay):
        findings.append({"severity": "major", "message": "results.tex differs from what the registry renders: run `paper results` (something edited the generated file)", "location": lay.rel(results_tex)})
    majors = [f for f in findings if f["severity"] == "major"]
    if majors:
        raise GateError(f"paper verify: {len(majors)} problem(s), {len(findings) - len(majors)} warning(s)", findings=findings)
    return {"status": "verified", "findings": findings, "result_files": [lay.rel(p) for p in rfiles], "tex_files": [lay.rel(p) for p in files]}
