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

import copy
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
    ``inline_svg`` carries a LaTeXML-drawn picture (tikz etc.) that exists nowhere
    but in the page; ``remote`` is empty for those.
    """

    id: str
    kind: str  # "img" | "object" | "picture"
    remote: str
    local: str
    inline_svg: str | None = None

    @property
    def source_relpath(self) -> str:
        """``remote`` minus the leading ``<id>v<n>/`` -- the path as the e-print knows it."""
        parts = PurePosixPath(self.remote).parts
        return str(PurePosixPath(*parts[1:])) if len(parts) > 1 else self.remote


def local_name(relpath: str) -> str:
    """Flatten ``figs/plot.png`` to ``figs_plot`` so two ``plot.png`` in different directories never collide."""
    return "_".join(PurePosixPath(relpath).with_suffix("").parts)


@dataclass
class Adapted:
    html: str
    title: str
    image_dir: str = ""
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
    out = Adapted(html="", title="", image_dir=image_dir)
    counts = out.counts

    for sel in _DROP_SELECTORS:
        for el in article.select(sel):
            el.decompose()

    # Order matters: footnotes first (they live inside paragraphs, authors and captions),
    # then equations (tables that are not tables), then structures that contain math
    # as text (listings), then figures/tables, then everything else.
    counts["footnotes"] = _footnotes(soup, article)
    counts["equations"] = _equations(soup, article)
    _span_tables(soup, article)
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
                if p.contents:
                    p.append(NavigableString(" "))  # two math cells in one align row must not glue into $$a$$$$b$$
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


_SPAN_TABLE_TAGS = {"ltx_tabular": "table", "ltx_thead": "thead", "ltx_tbody": "tbody", "ltx_tfoot": "tfoot", "ltx_tr": "tr", "ltx_td": "td"}


def _span_tables(soup: BeautifulSoup, article: Tag) -> None:
    """A table inside ``\\resizebox``/``\\scalebox`` (or any inline context) comes out of LaTeXML as
    ``<span class=ltx_tabular><span class=ltx_tr><span class=ltx_td>`` -- no table elements at all,
    so pandoc sees one run of inline text (2608.25593 Table 2, 2503.17523 appendix tables).
    Rebuild the real elements; ``ltx_colspan_N`` carries the span width."""
    for el in article.find_all("span", class_=_SPAN_TABLE_TAGS.keys()):
        cls = _classes(el)
        for c, name in _SPAN_TABLE_TAGS.items():
            if c in cls:
                el.name = name
                break
        if el.name == "td":
            if "ltx_th" in cls:
                el.name = "th"
            for c in cls:
                m = re.fullmatch(r"ltx_colspan_(\d+)", c)
                if m:
                    el["colspan"] = m.group(1)
    # a <table> must not sit inside <p>/<span>: lift it out so the HTML parser keeps it whole
    for table in article.find_all("table"):
        block = table
        while block.parent is not None and block.parent.name in ("p", "span", "em", "strong"):
            block = block.parent
        if block is not table:
            block.insert_before(table.extract())


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
    # Anything still drawn as SVG (tikz, pgfplots) exists only in this page: keep the markup as an asset.
    for n, svg in enumerate(article.select("svg.ltx_picture"), start=1):
        out.pictures_dropped += 1
        markup = str(svg)
        if 'xmlns=' not in markup[:200]:
            markup = markup.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        pid = svg.get("id") or f"picture{n}"
        fig = Figure(id=pid, kind="picture", remote="", local=f"{_image_dir_of(out)}/{pid}.svg", inline_svg=markup)
        out.figures.append(fig)
        p = _new(soup, "p")
        p.append(_new(soup, "img", src=fig.local, alt=""))
        svg.replace_with(p)


def _image_dir_of(out: Adapted) -> str:
    return out.image_dir


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
        fig = Figure(id=g.get("id", ""), kind=kind, remote=remote, local="")
        fig.local = f"{image_dir}/{local_name(fig.source_relpath)}.png"
        out.figures.append(fig)
        img = _new(soup, "img", src=fig.local, alt="")
        if g.find_parent(["p", "span", "em", "strong", "a", "td", "th", "li", "figcaption"]) is not None and g.find_parent("figure") is None:
            g.replace_with(img)  # an icon inside a sentence (e.g. a GitHub logo before a URL) must not split the paragraph
        else:
            p = _new(soup, "p")
            p.append(img)
            g.replace_with(p)

    figures = article.select("figure")
    n_fig = sum(1 for f in figures if f.find_parent("figure") is None and _has_class(f, "ltx_figure"))
    n_tab = sum(1 for f in figures if _has_class(f, "ltx_table"))
    # Innermost first, so a sub-table's caption stays with its own table (codex review, 2026-09-05).
    for fig in reversed(figures):
        is_table = _has_class(fig, "ltx_table")
        for cap in fig.find_all("figcaption"):
            if cap.find_parent("figure") is not fig:
                continue
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
    for fig in figures:
        if fig.parent is not None:
            fig.name = "div"

    for table in article.select("table"):
        if table.find_parent("table") is not None:
            continue
        _promote_header(soup, table)  # before group marking, so the promoted header row is not read as a rule
        _flatten_cells(soup, table)
    return n_fig, n_tab


def _flatten_cells(soup: BeautifulSoup, table: Tag) -> None:
    """Pipe tables hold inline content only: a cell with ``<br>``, paragraphs, lists or a
    nested table makes pandoc emit the literal ``[TABLE]`` and drop every cell (measured on
    2503.17523's transcript tables). Flatten such cells to one inline run instead."""
    for inner in table.find_all("table"):
        # a table inside a cell is almost always a line-breaking trick (\\makecell, a two-row header):
        # join with spaces so the cell reads as the phrase it was, and the coverage check still matches it
        rows = [" ".join(_text(c) for c in tr.find_all(["td", "th"])) for tr in inner.find_all("tr")]
        inner.replace_with(NavigableString(" ".join(r for r in rows if r)))
    for cell in table.find_all(["td", "th"]):
        for br in cell.find_all("br"):
            br.replace_with(NavigableString(" "))
        for block in cell.find_all(["p", "div", "ul", "ol", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"]):
            block.insert_before(NavigableString(" "))
            block.unwrap()
    _expand_rowspans(soup, table)
    _mark_row_groups(soup, table)
    for th in table.find_all("th"):
        # a <th> in the body (LaTeXML row headers, or a header cell cloned down by rowspan) makes
        # pandoc read a multi-row header, which no pipe table can carry: the whole table then
        # degrades to one paragraph per cell (1706.03762, Table 3).
        if th.find_parent("thead") is None:
            th.name = "td"


def _expand_rowspans(soup: BeautifulSoup, table: Tag) -> None:
    """A pipe table has no rowspan, so pandoc leaves the spanned rows blank and a reader can no
    longer tell which category a row belongs to (codex review of 2503.17523, Table 1). Repeat the
    spanning cell's content in each row it covered instead."""
    rows = table.find_all("tr")
    pending: dict[int, list[tuple[int, Tag]]] = {}
    for i, tr in enumerate(rows):
        for col, clone in sorted(pending.pop(i, []), key=lambda x: x[0]):
            cells = tr.find_all(["td", "th"], recursive=False)
            if col < len(cells):
                cells[col].insert_before(clone)
            else:
                tr.append(clone)
        for col, cell in enumerate(tr.find_all(["td", "th"], recursive=False)):
            try:
                span = int(cell.get("rowspan") or 1)
            except ValueError:
                span = 1
            if span > 1:
                del cell["rowspan"]
                for k in range(1, span):
                    if i + k < len(rows):
                        clone = copy.copy(cell)
                        clone["data-rowspan-clone"] = "1"  # not the author's cell: ignored when reading rules
                        pending.setdefault(i + k, []).append((col, clone))


def _mark_row_groups(soup: BeautifulSoup, table: Tag) -> None:
    """LaTeXML keeps a \\midrule as ``ltx_border_t`` on the cells below it. The rule is the
    author's grouping of rows (three examples of three flights each, codex review), so an empty
    row stands in for it -- the one boundary marker a pipe table can carry."""
    body_rows = [tr for tr in table.find_all("tr") if tr.find_parent("thead") is None]
    for tr in body_rows[1:]:
        own = [c for c in tr.find_all(["td", "th"], recursive=False) if not c.get("data-rowspan-clone")]
        if not own:
            continue
        ruled = all(any(c in _classes(cell) for c in ("ltx_border_t", "ltx_border_tt")) for cell in own)
        if ruled:
            n = len(tr.find_all(["td", "th"], recursive=False))
            sep = _new(soup, "tr")
            for _ in range(n):
                sep.append(_new(soup, "td", ""))
            tr.insert_before(sep)


def _promote_header(soup: BeautifulSoup, table: Tag) -> None:
    """Give every table a <thead>: pandoc otherwise emits an empty header row, and a first row that
    LaTeXML already marked with <th> would be demoted to the body by the multi-row-header guard."""
    if table.find("thead") is not None:
        return
    first = table.find("tr")
    if first is None:
        return
    for td in first.find_all("td", recursive=False):
        td.name = "th"
    thead = _new(soup, "thead")
    anchor = first.parent if first.parent is not None and first.parent.name == "tbody" else first
    anchor.insert_before(thead)  # a <thead> nested inside <tbody> is invalid and makes pandoc drop the table
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
    title = ""
    stray_refs: list[Tag] = []  # footnote marks on the title or between authors (thanks, equal contribution)
    if title_el is not None:
        stray_refs.extend(a.extract() for a in title_el.select("a.footnote-ref"))
        title = _text(title_el)
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
            creator.extract()
        stray_refs.extend(a.extract() for a in authors.select("a.footnote-ref"))
        p = _new(soup, "p")
        p.append(BeautifulSoup(", ".join(parts), "html.parser"))
        for a in stray_refs:
            p.append(a)  # keeps the definition referenced, otherwise pandoc drops the footnote body
        stray_refs = []
        authors.replace_with(p)
    elif stray_refs and title_el is not None:
        p = _new(soup, "p")
        for a in stray_refs:
            p.append(a)
        h1.insert_after(p)

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
