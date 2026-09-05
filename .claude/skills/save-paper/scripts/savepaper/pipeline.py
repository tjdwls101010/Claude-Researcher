"""``save`` as one transaction: resolve -> fetch -> convert -> check -> (describe) -> publish -> index.

Nothing in ``papers/`` changes until the staged file has been checked; the
index is regenerated only after publish. A run that ends with exit 6 has
saved the file but without ``verified`` -- there is no silent success.
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import assets as assets_mod
from .arxiv import ArxivClient, Resolved, resolve, safe_id
from .check import check, count_tex_bibitems
from .errors import EXIT_OK, EXIT_UNVERIFIED, ConvertError, FetchError
from .frontmatter import build_source_frontmatter, dump, fingerprint, now_iso
from .index import write_index
from .latexml import ADAPTER_VERSION, adapt
from .pandoc import WRITER, html_to_markdown, pandoc_version
from .pdfroute import KNOWN_LOSSES as PDF_LOSSES
from .pdfroute import pdf_to_markdown
from .publish import Layout, Staging, existing_fingerprint, existing_version

Log = Callable[[str], None]


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class Outcome:
    id: str
    version: Optional[int] = None
    route: Optional[str] = None
    coverage: Optional[float] = None
    verified: bool = False
    exit: int = EXIT_OK
    path: Optional[str] = None
    losses: list[str] = field(default_factory=list)
    status: str = ""  # saved | saved-unverified | up-to-date | new-version-available
    figures: Optional[str] = None  # "28/28"
    describe: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        parts = [f"{self.status}", f"id={self.id}v{self.version}", f"route={self.route}"]
        if self.coverage is not None:
            parts.append(f"coverage={self.coverage}")
        parts.append(f"verified={'yes' if self.verified else 'no'}")
        if self.figures:
            parts.append(f"figures={self.figures}")
        parts.append(f"losses={self.losses}")
        if self.describe:
            parts.append(f"described={self.describe.get('count')}/{self.describe.get('count', 0) + self.describe.get('failed', 0)} cost=${self.describe.get('cost', 0)}")
        if self.path:
            parts.append(self.path)
        for w in self.warnings:
            if "alt text skipped" in w:
                parts.append(f"WARNING: {w}")
        return "  ".join(parts)


def save_one(
    ref: str,
    layout: Layout,
    client: ArxivClient,
    route: str = "auto",
    version: Optional[int] = None,
    force: bool = False,
    with_assets: bool = True,
    describe: bool = False,
    describe_model: Optional[str] = None,
    api_key: Optional[str] = None,
    log: Log = _stderr,
) -> Outcome:
    resolved = resolve(ref, client, version=version)
    sid = safe_id(resolved.id)
    out = Outcome(id=resolved.id, version=resolved.version)
    final_md = layout.source_md(sid)
    log(f"resolved {ref!r} -> {resolved.vid}: {resolved.meta.title}")

    have = existing_version(final_md)
    if have is not None and have != resolved.version and not (force or version):
        out.status = "new-version-available"
        out.path = str(final_md)
        out.warnings.append(f"have v{have}, arXiv latest is v{resolved.version}; pass --version {resolved.version} or --force to re-save")
        log(out.warnings[-1])
        return out

    options = {"assets": with_assets, "writer": WRITER}
    with Staging(layout, resolved.vid) as st:
        html_resp = None
        if route in ("auto", "html"):
            html_resp = client.fetch_html(resolved.id, resolved.version)
            if html_resp.status_code == 404:
                if route == "html":
                    raise FetchError(f"arXiv has no HTML for {resolved.vid} (404); try --route pdf")
                log(f"no HTML for {resolved.vid}; falling back to PDF route")
                html_resp = None
            elif html_resp.status_code != 200:
                raise FetchError(f"HTML fetch returned {html_resp.status_code} for {resolved.vid}")

        if html_resp is not None:
            _html_route(resolved, st, layout, client, html_resp, with_assets, force, options, out, log)
        else:
            _pdf_route(resolved, st, client, force, options, out, log)
        if out.status == "up-to-date":
            return out

        carried = carry_over_alts(final_md, st.md)
        if carried:
            log(f"kept {carried} figure alt text(s) from the previous conversion")

        if describe:
            from .describe import describe_markdown

            if not api_key:
                out.warnings.append("figure alt text skipped: no OPENROUTER_API_KEY (copy scripts/savepaper/.env.example to .env there); run `describe <id>` later")
                log(out.warnings[-1])
            else:
                stats = describe_markdown(st.md, api_key, model=describe_model, log=log)
                out.describe = {"count": stats.count, "failed": stats.failed, "model": stats.model, **stats.usage}

        published = st.publish(sid)
        out.path = str(published)
    write_index(layout.papers_dir)
    out.status = "saved" if out.verified else "saved-unverified"
    out.exit = EXIT_OK if out.verified else EXIT_UNVERIFIED
    log(out.summary())
    return out


def _html_route(resolved: Resolved, st: Staging, layout: Layout, client: ArxivClient, html_resp, with_assets, force, options, out: Outcome, log: Log) -> None:
    page = html_resp.content.decode("utf-8", "replace")
    sha = hashlib.sha256(html_resp.content).hexdigest()
    converter = f"{pandoc_version()}; {ADAPTER_VERSION}"
    fp = fingerprint(resolved.id, resolved.version, sha, converter, options)
    if not force and existing_fingerprint(layout.source_md(safe_id(resolved.id))) == fp:
        out.status = "up-to-date"
        out.route = "html"
        out.path = str(layout.source_md(safe_id(resolved.id)))
        out.verified = True
        log(f"up to date: {out.path} (fingerprint {fp})")
        return

    out.route = "html"
    image_dir_rel = f"images/{resolved.vid}"
    adapted = adapt(page, image_dir=image_dir_rel)
    losses: list[str] = []

    tex_bibitems = None
    html = adapted.html
    if with_assets and adapted.figures:
        eprint = client.fetch_eprint(resolved.id, resolved.version)
        if eprint.status_code == 200:
            ex = assets_mod.extract_eprint(eprint.content, st.sources)
            if ex.rejected:
                losses.append(f"e-print members rejected: {len(ex.rejected)}")
            tex_bibitems = count_tex_bibitems(st.sources)
        else:
            log(f"e-print fetch returned {eprint.status_code}; figures will come from arxiv.org/html only")

        def fetch_remote(rel: str) -> Optional[bytes]:
            r = client.get(f"https://arxiv.org/html/{rel}")
            return r.content if r.status_code == 200 else None

        results = assets_mod.materialize(adapted.figures, st.sources, st.dir, fetch_remote)
        ok = [r for r in results if r.path is not None]
        out.figures = f"{len(ok)}/{len(results)}"
        for r in results:
            if r.status == "missing":
                losses.append(f"figure {r.figure.id or r.figure.remote}: {r.note}")
            elif r.status == "svg-fallback":
                losses.append(f"figure {r.figure.id or r.figure.remote}: SVG only ({r.note})")
        mapping = assets_mod.path_mapping(results)
        if mapping:
            html = adapted.rewrite_figure_paths(mapping)
        log(f"figures: {out.figures} ({sum(1 for r in results if r.status == 'rendered')} rendered from e-print)")
    elif adapted.figures:
        out.figures = f"0/{len(adapted.figures)}"
        losses.append(f"figures skipped (--no-assets): {len(adapted.figures)}")
        html = adapted.rewrite_figure_paths({f.local: f"https://arxiv.org/html/{f.remote}" for f in adapted.figures if f.remote})

    body = html_to_markdown(html)
    report = check(page, body, tex_bibitems=tex_bibitems)
    out.coverage = report.coverage
    out.verified = report.passed
    out.warnings.extend(report.warnings)
    if adapted.unparsed_math:
        losses.append(f"unparsed math kept as TeX: {adapted.unparsed_math}")
    if not report.passed:
        losses.append(f"coverage check failed: {len(report.missing)} block(s) missing")
        for m in report.missing[:10]:
            log(f"  missing {m['kind']} {m['id']}: {m['preview']}")
    for w in report.warnings:
        log(f"  warning: {w}")
    out.losses = losses

    conversion = {
        "route": "html",
        "converter": converter,
        "coverage": report.coverage,
        "known_losses": losses,
        "fingerprint": fp,
        "check": {"total": report.total, "matched": report.matched, "counts": report.counts},
    }
    if report.missing:
        conversion["check"]["missing"] = report.missing[:50]
    sources = [
        {
            "id": "arxiv-html",
            "resource": f"https://arxiv.org/html/{resolved.vid}",
            "last_modified": _http_date(html_resp),
            "sha256": sha,
        },
        {"id": "arxiv-src", "resource": f"https://arxiv.org/e-print/{resolved.vid}"},
    ]
    fm = build_source_frontmatter(resolved, sources=sources, conversion=conversion, verified=report.passed, generated_at=now_iso())
    st.md.write_text(dump(fm, body), encoding="utf-8")


def _pdf_route(resolved: Resolved, st: Staging, client: ArxivClient, force, options, out: Outcome, log: Log) -> None:
    out.route = "pdf"
    pdf = client.fetch_pdf(resolved.id, resolved.version)
    if pdf.status_code != 200:
        raise FetchError(f"PDF fetch returned {pdf.status_code} for {resolved.vid}")
    sha = hashlib.sha256(pdf.content).hexdigest()
    converter = "anydoc (npx @firecrawl/anydoc)"
    fp = fingerprint(resolved.id, resolved.version, sha, converter, options)
    from .publish import existing_fingerprint as _efp

    if not force and _efp(st.layout.source_md(safe_id(resolved.id))) == fp:
        out.status = "up-to-date"
        out.path = str(st.layout.source_md(safe_id(resolved.id)))
        log(f"up to date: {out.path}")
        return
    pdf_path = st.dir / "paper.pdf"
    pdf_path.write_bytes(pdf.content)
    body = pdf_to_markdown(pdf_path)
    out.losses = list(PDF_LOSSES)
    out.verified = False
    conversion = {"route": "pdf", "converter": converter, "coverage": None, "known_losses": out.losses, "fingerprint": fp}
    sources = [{"id": "arxiv-pdf", "resource": f"https://arxiv.org/pdf/{resolved.vid}", "last_modified": _http_date(pdf), "sha256": sha}]
    fm = build_source_frontmatter(resolved, sources=sources, conversion=conversion, verified=False, generated_at=now_iso())
    st.md.write_text(dump(fm, body), encoding="utf-8")
    log("PDF route: text and tables only; math and figures are known losses, file is unverified")


_IMG_LINK = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)\)")


def carry_over_alts(previous_md: Path, staged_md: Path) -> int:
    """Copy alt texts from the previously published file into the staged one, by image path.

    A re-conversion (``--force``, adapter upgrade) produces empty alts again; the descriptions
    already paid for still fit the same PNGs, so they are kept and only new figures get described.
    """
    if not previous_md.exists():
        return 0
    old_alts = {m.group("path"): m.group("alt") for m in _IMG_LINK.finditer(previous_md.read_text(encoding="utf-8")) if m.group("alt").strip()}
    if not old_alts:
        return 0
    text = staged_md.read_text(encoding="utf-8")
    count = 0

    def fill(m):
        nonlocal count
        if not m.group("alt").strip() and m.group("path") in old_alts:
            count += 1
            return f"![{old_alts[m.group('path')]}]({m.group('path')})"
        return m.group(0)

    new_text = _IMG_LINK.sub(fill, text)
    if count:
        staged_md.write_text(new_text, encoding="utf-8")
    return count


def _http_date(resp) -> str:
    headers = getattr(resp, "headers", {}) or {}
    return headers.get("Last-Modified", "")
