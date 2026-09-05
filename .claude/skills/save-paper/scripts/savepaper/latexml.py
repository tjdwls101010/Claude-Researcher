"""LaTeXML (arXiv HTML) DOM -> clean HTML that pandoc serialises faithfully.

Division of labour: this module decides *what survives* (which ``ltx_*``
structures become which plain HTML), pandoc decides *how it is written*
(headings, lists, pipe tables, ``$..$`` from MathML annotations, escapes).

Governing principle for anything not listed in ``adapt``: unwrap so the text
survives, and let the coverage check (``check.py``) report what went missing.
A structure gets an explicit rule only when blind unwrapping would destroy a
boundary the reader needs (footnote bodies, listing lines, theorem titles,
equation rows, figure/asset links) -- these were confirmed on three papers in
the plan session, 2026-09-05.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from bs4 import BeautifulSoup, NavigableString, Tag

from .errors import ConvertError

ADAPTER_VERSION = "latexml-adapter 0.1"

# Chrome around the article that carries no paper content.
_DROP_SELECTORS = [
    "nav",
    "header",
    "footer",
    "script",
    "style",
    "button",
    ".ltx_TOC",
    ".ltx_page_navbar",
    ".ltx_page_logo",
    ".ltx_page_footer",
    ".ltx_pagination",
    ".ltx_role_newpage",
    ".ltx_LaTeXML_logo",
    ".ltx_rule",
]


@dataclass
class Figure:
    """One graphic the source layer must own locally.

    ``remote`` is the DOM's ``src``/``data`` (relative to ``https://arxiv.org/html/``);
    ``local`` is where the Markdown expects it (always ``.png`` -- ``assets.py``
    may fall back to another suffix, then ``rewrite_figure_paths`` fixes the HTML).
    """

    id: str
    kind: str  # "img" | "object"
    remote: str
    local: str


@dataclass
class Adapted:
    html: str
    title: str
    figures: list[Figure] = field(default_factory=list)
    unparsed_math: int = 0
    pictures_dropped: int = 0
    counts: dict = field(default_factory=dict)

    def rewrite_figure_paths(self, mapping: dict[str, str]) -> str:
        """Return ``html`` with figure ``src`` values swapped per ``mapping`` (local -> actual)."""
        soup = BeautifulSoup(self.html, "lxml")
        for img in soup.find_all("img"):
            src = img.get("src")
            if src in mapping:
                img["src"] = mapping[src]
        body = soup.body
        return "".join(str(c) for c in body.contents) if body else str(soup)


# --- helpers ------------------------------------------------------------------


def _classes(el: Tag) -> list[str]:
    cls = el.get("class") or []
    return cls if isinstance(cls, list) else [cls]


def _has_class(el: Tag, name: str) -> bool:
    return name in _classes(el)


def _text(el: Tag | NavigableString) -> str:
    return re.sub(r"\s+", " ", el.get_text() if isinstance(el, Tag) else str(el)).strip()


def math_tex(math: Tag) -> str:
    """The TeX LaTeXML stored on a ``<math>``: the annotation, else ``alttext``."""
    ann = math.find("annotation", attrs={"encoding": "application/x-tex"})
    if ann and ann.string is not None:
        return ann.get_text().strip()
    return (math.get("alttext") or "").strip()


def text_with_tex(el: Tag) -> str:
    """Plain text of ``el`` with each ``<math>`` replaced by its TeX (shared with check.py)."""
    parts: list[str] = []
    for node in el.descendants:
        if isinstance(node, NavigableString):
            if any(p.name == "math" for p in node.parents):
                continue
            parts.append(str(node))
        elif isinstance(node, Tag) and node.name == "math":
            parts.append(" " + math_tex(node) + " ")
    return re.sub(r"(?:[ \t]*\n[ \t]*)+", " ", "".join(parts))


def bibitem_text(li: Tag) -> str:
    """One reference as a single line: refnum tag + bibblocks, minus the 'Cited by' back-links."""
    pieces = []
    tag = li.find(class_="ltx_tag_bibitem")
    if tag:
        pieces.append(_text(tag))
    for block in li.find_all(class_="ltx_bibblock"):
        if _has_class(block, "ltx_bib_cited"):
            continue
        pieces.append(_text(block))
    return re.sub(r"\s+", " ", " ".join(p for p in pieces if p)).strip()


def _new(soup: BeautifulSoup, name: str, text: str | None = None, **attrs) -> Tag:
    tag = soup.new_tag(name, **attrs)
    if text is not None:
        tag.string = text
    return tag


def _move_children(src: Tag, dst: Tag) -> None:
    for child in list(src.contents):
        dst.append(child.extract())


# --- adapter ------------------------------------------------------------------


def adapt(html: str, image_dir: str) -> Adapted:
    """Turn one arXiv HTML page into pandoc-ready HTML plus the asset list.

    ``image_dir`` is the Markdown-relative directory figures will live in
    (``images/<id>v<n>``); the file name is the DOM basename with ``.png``.
    """
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("article.ltx_document")
    if article is None:
        raise ConvertError("no <article class=ltx_document> in HTML; not a LaTeXML page")
    out = Adapted(html="", title="")
    counts = out.counts

    for sel in _DROP_SELECTORS:
        for el in article.select(sel):
            el.decompose()

    # Order matters: footnotes first (they live inside paragraphs, authors and captions),
    # then equations (tables that are not tables), then structures that contain math
    # as text (listings), then figures/tables, then everything else.
    counts["footnotes"] = _footnotes(soup, article)
    counts["equations"] = _equations(soup, article)
    _foreign_objects(soup, article, out)
    counts["listings"] = _listings(soup, article)
    _theorems(soup, article)
    counts["figures"], counts["tables"] = _figures_and_tables(soup, article, out, image_dir)
    counts["bibitems"] = _bibliography(soup, article)
    out.title = _front_matter(soup, article)
    counts["sections"] = len(article.select(".ltx_title_section"))
    _lists(soup, article)
    _links(soup, article)
    out.unparsed_math = len(article.select("math.ltx_math_unparsed"))
    _unwrap_rest(soup, article)

    body = _new(soup, "div")
    _move_children(article, body)
    # LaTeX ``~`` arrives as U+00A0; a plain space reads and greps the same for an LLM.
    out.html = "".join(str(c) for c in body.contents).replace("\xa0", " ")
    return out


def _footnotes(soup: BeautifulSoup, article: Tag) -> int:
    notes = article.select(".ltx_note")
    if not notes:
        return 0
    section = _new(soup, "section", **{"class": "footnotes", "role": "doc-endnotes"})
    ol = _new(soup, "ol")
    section.append(ol)
    for n, note in enumerate(notes, start=1):
        content = note.select_one(".ltx_note_content") or note
        for junk in content.select(".ltx_note_mark, .ltx_tag_note, .ltx_note_type"):
            junk.decompose()
        li = _new(soup, "li", id=f"fn{n}", role="doc-endnote")
        p = _new(soup, "p")
        _move_children(content, p)
        li.append(p)
        ol.append(li)
        ref = _new(soup, "a", str(n), href=f"#fn{n}", id=f"fnref{n}", **{"class": "footnote-ref", "role": "doc-noteref"})
        note.replace_with(ref)
    article.append(section)
    return len(notes)


def _equations(soup: BeautifulSoup, article: Tag) -> int:
    """``table/span.ltx_equation`` and ``.ltx_equationgroup`` -> one ``<p>`` per equation row."""
    count = 0
    for container in article.select(".ltx_equation, .ltx_equationgroup"):
        if container.parent is None or container.find_parent(class_="ltx_equation") is not None:
            continue  # nested row of a group we handle from the outside
        if container.find_parent(class_="ltx_equationgroup") is not None:
            continue
        rows = [r for r in container.select(".ltx_eqn_row")] or [container]
        replacement = []
        for row in rows:
            p = _new(soup, "p")
            tag_text = " ".join(_text(t) for t in row.select(".ltx_tag_equation"))
            for t in row.select(".ltx_tag_equation"):
                t.decompose()
            cells = row.select(".ltx_eqn_cell") or [row]
            for cell in cells:
                for m in cell.find_all("math"):
                    m["display"] = "block"
                if cell.find("math") is None and _text(cell):
                    p.append(NavigableString(_text(cell) + " "))
                    continue
                _move_children(cell, p)
            if tag_text:
                p.append(NavigableString(" " + tag_text))
            if p.contents:
                replacement.append(p)
                count += 1
        for p in replacement:
            container.insert_before(p)
        container.decompose()
    return count


def _foreign_objects(soup: BeautifulSoup, article: Tag, out: Adapted) -> None:
    """``<svg class=ltx_picture>``: text boxes (tcolorbox etc.) carry their HTML in
    ``<foreignObject>`` -- keep that; a pure drawing has no text and is dropped."""
    while True:
        svg = next((s for s in article.select("svg.ltx_picture") if s.find("foreignobject") is not None), None)
        if svg is None:
            break
        div = _new(soup, "div")
        for fo in svg.find_all("foreignobject"):
            content = fo.select_one(".ltx_foreignobject_content") or fo
            _move_children(content, div)
        svg.replace_with(div)
    for svg in article.select("svg.ltx_picture"):
        out.pictures_dropped += 1
        svg.decompose()


def _listings(soup: BeautifulSoup, article: Tag) -> int:
    count = 0
    for listing in article.select(".ltx_listing"):
        lines = listing.select(".ltx_listingline")
        if not lines:
            lines = [listing]
        text_lines = [text_with_tex_inline(line).strip() for line in lines]
        pre = _new(soup, "pre")
        code = _new(soup, "code", "\n".join(text_lines))
        pre.append(code)
        listing.replace_with(pre)
        count += 1
    return count


def text_with_tex_inline(el: Tag) -> str:
    """Listing line text with ``<math>`` written as ``$tex$`` so pandoc does not see MathML inside code."""
    parts: list[str] = []
    for node in el.descendants:
        if isinstance(node, NavigableString):
            if any(p.name == "math" for p in node.parents):
                continue
            parts.append(str(node))
        elif isinstance(node, Tag) and node.name == "math":
            parts.append(f"${math_tex(node)}$")
    return re.sub(r"(?:[ \t]*\n[ \t]*)+", " ", "".join(parts))


def _theorems(soup: BeautifulSoup, article: Tag) -> None:
    for box in article.select(".ltx_theorem, .ltx_proof"):
        title = box.find(class_="ltx_title")
        bq = _new(soup, "blockquote")
        if title is not None:
            label = _text(title).rstrip(".").strip()
            first_p = box.find("p")
            strong = _new(soup, "strong", f"{label}.")
            title.decompose()
            if first_p is not None:
                first_p.insert(0, NavigableString(" "))
                first_p.insert(0, strong)
            else:
                p = _new(soup, "p")
                p.append(strong)
                box.insert(0, p)
        _move_children(box, bq)
        box.replace_with(bq)


def _figures_and_tables(soup: BeautifulSoup, article: Tag, out: Adapted, image_dir: str) -> tuple[int, int]:
    # Graphics first, in document order, so panel order is preserved.
    for g in article.select("img.ltx_graphics, object.ltx_graphics"):
        kind = "img" if g.name == "img" else "object"
        remote = g.get("src") if kind == "img" else g.get("data")
        if not remote:
            g.decompose()
            continue
        stem = PurePosixPath(remote).stem
        local = f"{image_dir}/{stem}.png"
        out.figures.append(Figure(id=g.get("id", ""), kind=kind, remote=remote, local=local))
        p = _new(soup, "p")
        p.append(_new(soup, "img", src=local, alt=""))
        g.replace_with(p)

    n_fig = n_tab = 0
    for fig in article.select("figure"):
        if fig.find_parent("figure") is not None:
            continue
        is_table = _has_class(fig, "ltx_table")
        if is_table:
            n_tab += 1
        elif _has_class(fig, "ltx_figure"):
            n_fig += 1
        for cap in fig.select("figcaption"):
            for span in cap.find_all("span"):  # bold/italic runs inside an italic caption only add ***
                if span.find_parent("math") is None:
                    span.unwrap()
            p = _new(soup, "p")
            em = _new(soup, "em")
            _move_children(cap, em)
            p.append(em)
            if is_table:
                cap.decompose()
                fig.insert(0, p)  # caption above the table
            else:
                cap.replace_with(p)
        for inner in fig.select("figure"):
            inner.unwrap()
        fig.name = "div"

    for table in article.select("table"):
        _promote_header(soup, table)
    return n_fig, n_tab


def _promote_header(soup: BeautifulSoup, table: Tag) -> None:
    if table.find("thead") is not None or table.find("th") is not None:
        return
    first = table.find("tr")
    if first is None:
        return
    for td in first.find_all("td", recursive=False):
        td.name = "th"
    thead = _new(soup, "thead")
    first.insert_before(thead)
    thead.append(first.extract())


def _bibliography(soup: BeautifulSoup, article: Tag) -> int:
    items = article.select("li.ltx_bibitem")
    for li in items:
        line = bibitem_text(li)
        links = [a for a in li.select("a.ltx_bib_external, a.ltx_url") if a.get("href", "").startswith("http")]
        li.clear()
        li.append(NavigableString(line))
        for a in links:
            li.append(NavigableString(" "))
            li.append(_new(soup, "a", a.get("href"), href=a.get("href")))
    return len(items)


def _front_matter(soup: BeautifulSoup, article: Tag) -> str:
    title_el = article.select_one("h1.ltx_title_document") or article.select_one("h1.ltx_title")
    title = _text(title_el) if title_el else ""
    if title_el is not None:
        h1 = _new(soup, "h1", title)
        title_el.replace_with(h1)

    authors = article.select_one(".ltx_authors")
    if authors is not None:
        parts = []
        for creator in authors.select(".ltx_creator"):
            name_el = creator.select_one(".ltx_personname")
            name = _text(name_el) if name_el else _text(creator)
            affs = [_text(a).replace("Affiliation:", "").strip() for a in creator.select(".ltx_role_affiliation")]
            affs = [a for a in affs if a]
            refs = "".join(str(a) for a in creator.select("a.footnote-ref"))
            entry = name + (f" ({'; '.join(affs)})" if affs else "")
            parts.append(entry + refs)
        p = _new(soup, "p")
        p.append(BeautifulSoup(", ".join(parts), "html.parser"))
        authors.replace_with(p)

    abstract = article.select_one(".ltx_abstract")
    if abstract is not None:
        head = abstract.find(class_="ltx_title")
        h2 = _new(soup, "h2", _text(head) if head else "Abstract")
        if head is not None:
            head.replace_with(h2)
        else:
            abstract.insert(0, h2)
    return title


def _lists(soup: BeautifulSoup, article: Tag) -> None:
    for tag in article.select("li > .ltx_tag_item, li > * > .ltx_tag_item"):
        text = _text(tag)
        li = tag.find_parent("li")
        parent = li.parent if li is not None else None
        if text in {"•", "◦", "–", "-", "∗", "*"} or (parent is not None and parent.name == "ol" and re.fullmatch(r"\(?\d+[.)]?", text)):
            tag.decompose()
        else:
            tag.replace_with(NavigableString(text + " "))


def _links(soup: BeautifulSoup, article: Tag) -> None:
    for a in article.find_all("a"):
        if _has_class(a, "footnote-ref"):
            continue
        href = a.get("href", "")
        if href.startswith("#") or not href:
            a.unwrap()
        else:
            for k in list(a.attrs):
                if k != "href":
                    del a[k]
    for cite in article.find_all("cite"):
        cite.unwrap()


_INLINE_MAP = {
    "ltx_font_bold": "strong",
    "ltx_font_italic": "em",
    "ltx_font_typewriter": "code",
}


def _unwrap_rest(soup: BeautifulSoup, article: Tag) -> None:
    """Everything still carrying an ``ltx_*`` wrapper collapses to its text."""
    for span in article.find_all("span"):
        if span.find_parent("math") is not None:
            continue
        cls = _classes(span)
        new_name = next((_INLINE_MAP[c] for c in cls if c in _INLINE_MAP), None)
        if new_name and span.get_text(strip=True):
            span.name = new_name
            span.attrs = {}
        else:
            span.unwrap()
    for el in article.find_all(["div", "section", "article", "figure"]):
        if el.name == "section" and _has_class(el, "footnotes"):
            continue  # pandoc needs the wrapper to recognise footnote definitions
        el.unwrap()
    for el in article.find_all(True):
        if el.name == "math" or el.find_parent("math") is not None:
            continue
        keep = {"href", "src", "alt", "id", "colspan", "rowspan", "role", "class"} if el.name in {"a", "img", "td", "th", "li", "section"} else set()
        if el.name == "a" and not _has_class(el, "footnote-ref"):
            keep = {"href"}
        for k in list(el.attrs):
            if k not in keep:
                del el[k]
