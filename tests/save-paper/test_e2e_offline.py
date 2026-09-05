"""Whole pipeline on the recorded 2503.17523v3, with arXiv replaced by fixtures.

Seam: ``pipeline.save_one`` with an injected session and clock. Real pandoc and
pdftoppm run; only the network is faked.
"""

import re
from pathlib import Path

import pytest

from savepaper import arxiv, pipeline
from savepaper.arxiv import ArxivClient
from savepaper.errors import EXIT_OK, EXIT_UNVERIFIED, FetchError
from savepaper.frontmatter import parse
from savepaper.pipeline import save_one
from savepaper.publish import Layout

FIX = Path(__file__).parent / "fixtures" / "2503.17523v3"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}


class ArxivFake:
    """Routes URLs to fixtures; records every request."""

    def __init__(self, page=None, html_status=200):
        self.page = page if page is not None else (FIX / "page.html").read_bytes()
        self.html_status = html_status
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        if "export.arxiv.org/api/query" in url:
            return Resp(200, (FIX / "atom.xml").read_bytes())
        if url == "https://arxiv.org/html/2503.17523v3":
            return Resp(self.html_status, self.page, {"Last-Modified": "Thu, 15 Jan 2026 17:21:57 GMT"})
        if url == "https://arxiv.org/e-print/2503.17523v3":
            return Resp(200, (FIX / "eprint.tar.gz").read_bytes())
        if url == "https://arxiv.org/pdf/2503.17523v3":
            return Resp(200, b"%PDF-1.7 fake")
        if url.startswith("https://arxiv.org/html/2503.17523v3/"):
            return Resp(200, (FIX / "fig1_task.png").read_bytes())
        return Resp(404)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


@pytest.fixture(autouse=True)
def _rate():
    arxiv.reset_rate_limit()


def client(fake):
    c = Clock()
    return ArxivClient(session=fake, monotonic=c.monotonic, sleep=c.sleep)


def test_full_save_is_verified_with_all_figures(tmp_path):
    layout = Layout(tmp_path / "papers")
    fake = ArxivFake()
    logs = []
    out = save_one("https://arxiv.org/abs/2503.17523", layout, client(fake), log=logs.append)

    assert out.exit == EXIT_OK and out.status == "saved" and out.verified
    assert out.route == "html" and out.coverage == 1.0
    assert out.figures == "28/28"
    assert out.losses == ["unparsed math kept as TeX: 2"]

    md_path = layout.source_md("2503.17523")
    assert Path(out.path) == md_path
    fm, body = parse(md_path.read_text(encoding="utf-8"))
    assert fm["type"] == "Paper" and fm["arxiv"]["version"] == 3
    assert fm["verified"]["by"] == "process:save-paper-check"
    assert fm["conversion"]["route"] == "html" and fm["conversion"]["coverage"] == 1.0
    assert fm["conversion"]["check"]["counts"]["bibitems"] == 84
    assert fm["sources"][0]["sha256"] and fm["sources"][0]["last_modified"].startswith("Thu, 15 Jan 2026")
    assert "pandoc" in fm["conversion"]["converter"] and "latexml-adapter" in fm["conversion"]["converter"]

    assert body.startswith("# Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models")
    assert "## Abstract" in body and "## References" in body
    assert len(re.findall(r"^- .+\(\d{4}\)", body, re.M)) >= 84 or body.count("\n- ") >= 84
    images = sorted(layout.image_dir("2503.17523v3").glob("*"))
    assert len(images) == 28 and all(p.suffix == ".png" for p in images)
    assert all(p.read_bytes()[:8] == PNG_MAGIC for p in images)
    links = re.findall(r"!\[\]\((images/2503\.17523v3/[^)]+)\)", body)
    assert len(links) == 28
    assert {Path(l).name for l in links} == {p.name for p in images}

    index = layout.index_md.read_text(encoding="utf-8")
    assert "[Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models](sources/2503.17523.md)" in index
    assert "machine-confirmed" in index
    assert not (layout.sources_dir / ".staging").exists()
    # only the e-print was needed for figures: no per-figure downloads
    assert not any(u.startswith("https://arxiv.org/html/2503.17523v3/") for u in fake.calls)


def test_second_save_is_a_no_op_and_force_reconverts(tmp_path):
    layout = Layout(tmp_path / "papers")
    save_one("2503.17523", layout, client(ArxivFake()), log=lambda s: None)
    fake = ArxivFake()
    out = save_one("2503.17523", layout, client(fake), log=lambda s: None)
    assert out.status == "up-to-date" and out.exit == EXIT_OK
    assert not any("e-print" in u for u in fake.calls)  # stopped after the HTML fingerprint
    forced = save_one("2503.17523", layout, client(ArxivFake()), force=True, log=lambda s: None)
    assert forced.status == "saved"


def test_force_resave_keeps_existing_alt_texts(tmp_path):
    layout = Layout(tmp_path / "papers")
    save_one("2503.17523", layout, client(ArxivFake()), log=lambda s: None)
    md = layout.source_md("2503.17523")
    text = md.read_text()
    text = text.replace("![](images/2503.17523v3/fig1_task.png)", "![Diagram of the flight task.](images/2503.17523v3/fig1_task.png)", 1)
    text = text.replace("conversion:", "figures_described: {by: openrouter/test, at: '2026-09-06T00:00:00Z', count: 1, failed: 0}\nconversion:", 1)
    md.write_text(text)
    logs = []
    out = save_one("2503.17523", layout, client(ArxivFake()), force=True, log=logs.append)
    assert out.status == "saved"
    assert "![Diagram of the flight task.](images/2503.17523v3/fig1_task.png)" in md.read_text()
    assert any("kept 1 figure alt text" in l for l in logs)
    assert parse(md.read_text())[0]["figures_described"]["count"] == 1  # provenance of the kept alt survives


def test_newer_version_is_reported_not_overwritten(tmp_path):
    layout = Layout(tmp_path / "papers")
    save_one("2503.17523v3", layout, client(ArxivFake()), log=lambda s: None)
    # pretend the saved copy is v2
    md = layout.source_md("2503.17523")
    md.write_text(md.read_text().replace("version: 3", "version: 2", 1).replace("fingerprint: ", "fingerprint: old", 1))
    out = save_one("2503.17523", layout, client(ArxivFake()), log=lambda s: None)
    assert out.status == "new-version-available" and out.exit == EXIT_OK
    assert "have v2" in out.warnings[0]
    assert "version: 2" in md.read_text()  # untouched
    out2 = save_one("2503.17523", layout, client(ArxivFake()), version=3, log=lambda s: None)
    assert out2.status == "saved"


def test_lossy_conversion_is_saved_but_unverified(tmp_path):
    """Drop the abstract paragraph from pandoc's output: the file is still written, without ``verified``."""
    layout = Layout(tmp_path / "papers")
    original = pipeline.html_to_markdown

    def lossy(html):
        md = original(html)
        head, rest = md.split("Large language models (LLMs) are increasingly", 1)
        return head + rest.split("update its beliefs as it receives new information.", 1)[1]

    pipeline.html_to_markdown = lossy
    try:
        out = save_one("2503.17523", layout, client(ArxivFake()), with_assets=False, log=lambda s: None)
    finally:
        pipeline.html_to_markdown = original
    assert out.exit == EXIT_UNVERIFIED and out.status == "saved-unverified" and not out.verified
    assert out.coverage < 1.0
    fm, _ = parse(layout.source_md("2503.17523").read_text())
    assert "verified" not in fm
    assert fm["conversion"]["check"]["missing"][0]["id"] == "abstract1.1"
    assert any("coverage check failed" in l for l in out.losses)
    assert "unverified" in layout.index_md.read_text()


def test_describe_without_key_warns_and_still_saves(tmp_path):
    layout = Layout(tmp_path / "papers")
    out = save_one("2503.17523", layout, client(ArxivFake()), with_assets=False, describe=True, api_key=None, log=lambda s: None)
    assert out.exit == EXIT_OK and out.verified
    assert any("alt text skipped" in w for w in out.warnings)
    assert "WARNING" in out.summary()


def test_no_assets_points_images_at_arxiv(tmp_path):
    layout = Layout(tmp_path / "papers")
    out = save_one("2503.17523", layout, client(ArxivFake()), with_assets=False, log=lambda s: None)
    assert out.verified and out.figures == "0/28"
    body = parse(layout.source_md("2503.17523").read_text())[1]
    assert "![](https://arxiv.org/html/2503.17523v3/fig1_task.png)" in body
    assert not layout.images_dir.exists()


def test_pdf_route_when_html_is_404(tmp_path, monkeypatch):
    layout = Layout(tmp_path / "papers")
    monkeypatch.setattr(pipeline, "pdf_to_markdown", lambda pdf: "# Paper\n\n" + "text " * 200)
    out = save_one("2503.17523", layout, client(ArxivFake(html_status=404)), log=lambda s: None)
    assert out.route == "pdf" and out.exit == EXIT_UNVERIFIED and out.status == "saved-unverified"
    assert out.losses == ["math", "figures"]
    fm, body = parse(layout.source_md("2503.17523").read_text())
    assert "verified" not in fm
    assert fm["conversion"]["route"] == "pdf" and fm["conversion"]["known_losses"] == ["math", "figures"]
    assert fm["sources"][0]["id"] == "arxiv-pdf"
    assert "route:pdf" in layout.index_md.read_text()


def test_html_route_forced_and_404_is_fetch_error(tmp_path):
    layout = Layout(tmp_path / "papers")
    with pytest.raises(FetchError):
        save_one("2503.17523", layout, client(ArxivFake(html_status=404)), route="html", log=lambda s: None)
    assert not layout.papers_dir.exists() or not layout.source_md("2503.17523").exists()
