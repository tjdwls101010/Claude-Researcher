"""Completeness check: is every content block of the original HTML present in the Markdown?

Counting sections or bibitems only detects smoke. What catches truncation is
ordered content coverage: every paragraph, table cell, caption, reference,
footnote body, listing line and heading of the *untouched* DOM is normalised
and looked up as a substring of the normalised Markdown. A missing paragraph,
cell, caption, bibitem, footnote or listing line fails the check; unparsed
math and a bibitem-count mismatch against the TeX sources are warnings.

Limit, stated once: this guarantees fidelity to the HTML arXiv served, not
that the HTML is the whole paper. The TeX ``\\bibitem`` count is the one
independent signal we have, hence the warning.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag

from .latexml import bibitem_text, math_tex

FAIL_KINDS = {"paragraph", "cell", "caption", "bibitem", "footnote", "listingline", "heading"}
# Subtrees whose text is accounted for elsewhere or is metadata, not paper content: footnotes
# (checked as their own blocks), bibliography back-links, and the author block (names go to
# frontmatter; the name/email/affiliation mini-tables LaTeXML builds there are layout).
_SKIP_INSIDE = {"ltx_note", "ltx_note_mark", "ltx_tag_note", "ltx_note_type", "ltx_bib_cited", "ltx_authors"}
_DROP = ["nav", ".ltx_TOC", ".ltx_page_navbar", ".ltx_page_logo", ".ltx_page_footer", "script", "style"]


@dataclass
class Block:
    id: str
    kind: str
    text: str

    def preview(self, n: int = 60) -> str:
        return re.sub(r"\s+", " ", self.text)[:n]


@dataclass
class Report:
    total: int
    matched: int
    missing: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return round(self.matched / self.total, 4) if self.total else 1.0

    @property
    def passed(self) -> bool:
        return not any(m["kind"] in FAIL_KINDS for m in self.missing)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "coverage": self.coverage,
            "total": self.total,
            "matched": self.matched,
            "missing": self.missing,
            "warnings": self.warnings,
            "counts": self.counts,
        }


# --- text extraction from the original DOM -------------------------------------


def _classes(el: Tag) -> set[str]:
    cls = el.get("class") or []
    return set(cls if isinstance(cls, list) else [cls])


def _skipped(node, stop: Tag) -> bool:
    """True when ``node`` sits inside a subtree we account for elsewhere (footnotes, bib back-links)."""
    for p in node.parents:
        if p is stop:
            return False
        if isinstance(p, Tag) and (p.name == "math" or _classes(p) & _SKIP_INSIDE):
            return True
    return False


def block_text(el: Tag, inline_math: bool = False) -> str:
    """Text of ``el`` with ``<math>`` as its TeX and footnote subtrees left out."""
    parts: list[str] = []
    for node in el.descendants:
        if isinstance(node, Tag) and node.name == "math":
            if not _skipped(node, el):
                tex = math_tex(node)
                parts.append(f" ${tex}$ " if inline_math else f" {tex} ")
        elif isinstance(node, NavigableString) and not _skipped(node, el):
            parts.append(str(node))
    return "".join(parts)


def extract_blocks(html: str) -> tuple[list[Block], dict]:
    """Content blocks of a LaTeXML page in document order, plus structural counts."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("article.ltx_document") or soup
    for sel in _DROP:
        for el in root.select(sel):
            el.decompose()

    blocks: list[Block] = []
    seen_ids: set[int] = set()

    def add(el: Tag, kind: str, text: str):
        if id(el) in seen_ids:
            return
        seen_ids.add(id(el))
        text = text.strip()
        if text:
            blocks.append(Block(el.get("id") or f"{kind}#{len(blocks) + 1}", kind, text))

    for el in root.find_all(True):
        cls = _classes(el)
        name = el.name
        if _skipped(el, root) and "ltx_note_content" not in cls:
            continue
        if name in ("h1", "h2", "h3", "h4", "h5", "h6") and "ltx_title" in cls:
            add(el, "heading", block_text(el))
        elif "ltx_p" in cls and not el.find_parent(class_="ltx_p"):
            if el.find(class_="ltx_tabular") is None:  # a paragraph that only wraps a table: its cells are the blocks
                add(el, "paragraph", block_text(el))
        elif name in ("td", "th") or "ltx_td" in cls:
            if el.find("table") is None and el.find(class_="ltx_tabular") is None:  # nested table: checked via its own cells
                add(el, "cell", block_text(el))
        elif name == "figcaption":
            add(el, "caption", block_text(el))
        elif name == "li" and "ltx_bibitem" in cls:
            add(el, "bibitem", bibitem_text(el))
        elif "ltx_note_content" in cls:
            add(el, "footnote", block_text(el))
        elif "ltx_listingline" in cls:
            add(el, "listingline", block_text(el, inline_math=True))

    counts = {
        "sections": len(root.select(".ltx_title_section")),
        "figures": len([f for f in root.select("figure.ltx_figure") if f.find_parent("figure") is None]),
        "tables": len(root.select("figure.ltx_table")),
        "bibitems": len(root.select("li.ltx_bibitem")),
        "footnotes": len(root.select(".ltx_note")),
        "equations": len(root.select(".ltx_equation.ltx_eqn_row")) or len(root.select(".ltx_equation")),
        "listings": len(root.select(".ltx_listing")),
        "unparsed_math": len(root.select("math.ltx_math_unparsed")),
        # real data tables: not equation layout tables, and only the outermost of nested ones
        "data_tables": len(
            [
                t
                for t in root.find_all(["table", "span"], class_="ltx_tabular")  # span-based tables come from \\resizebox
                if not (_classes(t) & {"ltx_equation", "ltx_equationgroup", "ltx_eqn_table"})
                and t.find_parent(class_="ltx_tabular") is None
                and t.find_parent(class_="ltx_equation") is None
                and t.find_parent(class_="ltx_authors") is None
            ]
        ),
    }
    return blocks, counts


# --- normalisation ---------------------------------------------------------------

_STRIP_CHARS = str.maketrans("", "", "\\$*_|<>[]#`~()^")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\](?!:)")  # ``[^3]`` in the body; the DOM paragraph never contains the mark
_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)")  # an inline graphic has no text in the DOM; its alt is never checked
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # ``[text](url)``: the DOM has only the text
_ASCII_EQUIV = str.maketrans({"\u2212": "-", "\u2010": "-", "\u2011": "-", "\u00a0": " "})  # pandoc's MathML fallback writes a real minus sign


def normalize(text: str) -> str:
    """Collapse everything that Markdown serialisation may legitimately change.

    Whitespace, Markdown escapes/markers, math delimiters, the parentheses and
    carets pandoc adds for ``<sub>``/``<sup>`` (``LongMemEval_(S)``, ``^(†)``) and
    Unicode format characters (zero-width joiners, MathML's invisible operators)
    all go, and compatibility characters are folded (``x²`` -> ``x2``); what
    stays is the sequence of visible characters, which pandoc does not alter.
    """
    text = unicodedata.normalize("NFKC", text).translate(_ASCII_EQUIV)
    text = _MD_LINK.sub(r"\1", _IMAGE_LINK.sub("", _FOOTNOTE_REF.sub("", text))).translate(_STRIP_CHARS)
    text = "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch) != "Cf")
    return text


# --- check -----------------------------------------------------------------------


def check(html: str, markdown: str, tex_bibitems: Optional[int] = None) -> Report:
    blocks, counts = extract_blocks(html)
    norm_md = normalize(markdown)
    report = Report(total=len(blocks), matched=0, counts=counts)
    for b in blocks:
        if normalize(b.text) in norm_md:
            report.matched += 1
        else:
            report.missing.append({"id": b.id, "kind": b.kind, "preview": b.preview()})
    if counts["unparsed_math"]:
        report.warnings.append(f"{counts['unparsed_math']} math element(s) LaTeXML could not parse (TeX kept verbatim)")
    dom_tables = counts.get("data_tables", 0)
    md_tables = len(_PIPE_SEPARATOR.findall(markdown))
    if md_tables < dom_tables:
        report.warnings.append(
            f"{dom_tables - md_tables} of {dom_tables} table(s) did not come out as a pipe table (cells present, but as loose paragraphs; rows and columns cannot be attributed)"
        )
    if tex_bibitems is not None and counts["bibitems"] and tex_bibitems != counts["bibitems"]:
        report.warnings.append(
            f"TeX sources declare {tex_bibitems} \\bibitem entries but the HTML has {counts['bibitems']}; the HTML may not be the whole paper"
        )
    return report


_PIPE_SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$", re.M)
_BIBITEM_RE = re.compile(r"\\bibitem\b")


def count_tex_bibitems(extracted_dir: Path) -> Optional[int]:
    """``\\bibitem`` occurrences in the e-print: the .bbl if present, else the .tex files. None when no sources."""
    if not extracted_dir.exists():
        return None
    bbl = sum(len(_BIBITEM_RE.findall(p.read_text("utf-8", "replace"))) for p in extracted_dir.rglob("*.bbl"))
    if bbl:
        return bbl
    tex_files = list(extracted_dir.rglob("*.tex"))
    if not tex_files:
        return None
    n = sum(len(_BIBITEM_RE.findall(p.read_text("utf-8", "replace"))) for p in tex_files)
    return n or None  # a BibTeX-based paper ships .bib, not \bibitem: nothing to compare against
