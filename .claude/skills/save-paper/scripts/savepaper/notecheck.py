"""``note-check``: does the Korean note carry the source's numbers and equations?

A restructured note must keep every result number and every display equation
verbatim (they are never translated). This lists the ones the source body
states that the note does not contain, so the reviewer looks exactly there
instead of re-reading both files. It is a smoke detector, not a proof: prose
claims, tables and figure content are for the human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .check import normalize
from .frontmatter import parse

MIN_NOTE_CHARS = 3000
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)\s?(%|×|x\b)?")
_DISPLAY_MATH_RE = re.compile(r"^\$\$(.+?)\$\$", re.M | re.S)  # anchored: a stray ``$$`` mid-line must not desync the pairing
_SKIP_LINE = re.compile(r"^\s*(\||!\[|\$\$|#|>|- |\d+\.\s)")


@dataclass
class NoteReport:
    note_chars: int
    source_chars: int
    numbers_total: int
    numbers_missing: list[str]
    equations_total: int
    equations_missing: list[str]
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "note_chars": self.note_chars,
            "source_chars": self.source_chars,
            "numbers": {"total": self.numbers_total, "missing": self.numbers_missing},
            "equations": {"total": self.equations_total, "missing": self.equations_missing},
            "problems": self.problems,
        }


def _body_prose(body: str) -> str:
    """Body paragraphs only: no tables, images, display math, headings, lists or the References section."""
    cut = re.split(r"^## References\s*$", body, flags=re.M)[0]
    return "\n".join(l for l in cut.splitlines() if l.strip() and not _SKIP_LINE.match(l))


def result_numbers(prose: str) -> list[str]:
    """Numbers that read as results: decimals, percentages, ratios and thousands-separated counts. Years and small integers are skipped."""
    found = []
    for m in _NUM_RE.finditer(prose):
        num, unit = m.group(1), m.group(2) or ""
        if "." in num or "," in num or unit:
            found.append(num + unit)
        elif len(num) >= 3 and not re.fullmatch(r"(19|20)\d\d", num):
            found.append(num)
    seen = set()
    out = []
    for n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def note_check(source_path: Path, note_path: Path) -> NoteReport:
    src_fm, src_body = parse(source_path.read_text(encoding="utf-8"))
    note_fm, note_body = parse(note_path.read_text(encoding="utf-8"))
    prose = _body_prose(src_body)
    numbers = result_numbers(prose)
    note_norm = normalize(note_body)
    note_plain = note_body.replace(",", "")
    numbers_missing = [n for n in numbers if n not in note_body and n.replace(",", "") not in note_plain]
    equations = [normalize(e) for e in _DISPLAY_MATH_RE.findall(src_body)]
    eq_raw = _DISPLAY_MATH_RE.findall(src_body)
    equations_missing = [raw.strip()[:80] for raw, n in zip(eq_raw, equations) if n and n not in note_norm]
    report = NoteReport(
        note_chars=len(note_body),
        source_chars=len(src_body),
        numbers_total=len(numbers),
        numbers_missing=numbers_missing,
        equations_total=len(equations),
        equations_missing=equations_missing,
    )
    if note_fm.get("type") != "Paper Note":
        report.problems.append("note frontmatter lacks `type: Paper Note`")
    if not re.search(r"^# 🖇️", note_body, re.M):
        report.problems.append("note body does not start with `# 🖇️<title>`")
    if len(note_body) < MIN_NOTE_CHARS:
        report.problems.append(f"note body is {len(note_body)} chars; under {MIN_NOTE_CHARS} it cannot be a full restructuring")
    src_rel = f"/papers/sources/{source_path.name}"
    srcs = note_fm.get("sources") or []
    if not any(str(s.get("resource", "")).endswith(source_path.name) for s in srcs if isinstance(s, dict)):
        report.problems.append(f"note `sources` does not reference {src_rel}")
    return report
