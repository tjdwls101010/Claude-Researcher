"""Seam: ``check.check(original_html, markdown)`` -> Report.

The positive case runs the real adapter + pandoc on the recorded full page, so
this doubles as the proof that the adapter loses nothing on 2503.17523v3. The
negative cases mutate the Markdown and expect the report to name the block.
"""

import re
from pathlib import Path

import pytest

from savepaper import check as check_mod
from savepaper.assets import extract_eprint
from savepaper.check import check, count_tex_bibitems, extract_blocks, normalize
from savepaper.latexml import adapt
from savepaper.pandoc import html_to_markdown

FIX = Path(__file__).parent / "fixtures" / "2503.17523v3"


@pytest.fixture(scope="module")
def page():
    return (FIX / "page.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markdown(page):
    r = adapt(page, image_dir="images/2503.17523v3")
    return html_to_markdown(r.html)


def test_full_page_is_fully_covered(page, markdown):
    report = check(page, markdown)
    assert report.missing == []
    assert report.coverage == 1.0
    assert report.passed
    assert report.total > 450
    assert report.counts["bibitems"] == 84
    assert report.counts["figures"] == 27
    assert report.counts["tables"] == 12


def test_full_page_warns_about_unparsed_math_only(page, markdown):
    report = check(page, markdown, tex_bibitems=84)
    assert len(report.warnings) == 1
    assert "2 math element" in report.warnings[0]


def test_bibitem_mismatch_is_a_warning_not_a_failure(page, markdown):
    report = check(page, markdown, tex_bibitems=90)
    assert report.passed
    assert any("90" in w and "84" in w for w in report.warnings)


def test_removed_paragraph_is_reported_by_id(page, markdown):
    blocks, _ = extract_blocks(page)
    target = next(b for b in blocks if b.kind == "paragraph" and b.id == "S1.p1.1")
    sentence = target.text.strip()[:80]
    # find that paragraph in the markdown and cut it out
    idx = markdown.find(sentence[:40])
    assert idx > 0
    end = markdown.find("\n", idx)
    mutated = markdown[:idx] + markdown[end:]
    report = check(page, mutated)
    assert not report.passed
    assert report.coverage < 1.0
    ids = [m["id"] for m in report.missing]
    assert "S1.p1.1" in ids
    assert report.missing[0]["kind"] == "paragraph"
    assert report.missing[0]["preview"].startswith(target.preview()[:20])


def test_removed_table_cell_is_reported(page, markdown):
    assert "memory foam, solid wood" in markdown
    mutated = markdown.replace("memory foam, solid wood", "", 1)
    report = check(page, mutated)
    assert not report.passed
    assert any(m["kind"] == "cell" and "memory foam" in m["preview"] for m in report.missing)


def test_removed_bibitem_is_reported(page, markdown):
    line = next(l for l in markdown.splitlines() if l.startswith("- ") and "Kahneman" in l)
    report = check(page, markdown.replace(line, "", 1))
    assert not report.passed
    assert any(m["kind"] == "bibitem" for m in report.missing)


def test_missing_caption_fails_but_missing_math_warning_does_not():
    html = """<article class="ltx_document">
    <p class="ltx_p" id="p1">Hello <math class="ltx_math_unparsed"><semantics><mi>x</mi><annotation encoding="application/x-tex">\\weird{x}</annotation></semantics></math> world.</p>
    <figure class="ltx_figure"><figcaption class="ltx_caption">Figure 1: A caption.</figcaption></figure></article>"""
    ok = check(html, "Hello $\\weird{x}$ world.\n\n*Figure 1: A caption.*\n")
    assert ok.passed and ok.coverage == 1.0 and ok.warnings
    bad = check(html, "Hello $\\weird{x}$ world.\n")
    assert not bad.passed
    assert bad.missing[0]["kind"] == "caption"


@pytest.mark.parametrize(
    "dom, md",
    [
        ("under_score * star <tag> a|b 50% #hash", "under\\_score \\* star \\<tag\\> a\\|b 50% \\#hash"),
        ("Table\xa01 and\nline", "Table 1 and line"),
        ("x⁡(y)", "x(y)"),
        ("**bold** text", "bold text"),
    ],
)
def test_normalize_ignores_markdown_serialisation_noise(dom, md):
    assert normalize(dom) == normalize(md)


def test_footnote_body_is_checked_separately_from_its_paragraph():
    html = """<article class="ltx_document"><p class="ltx_p" id="p1">Main text<span class="ltx_note ltx_role_footnote" id="fn1"><sup class="ltx_note_mark">1</sup><span class="ltx_note_outer"><span class="ltx_note_content"><sup class="ltx_note_mark">1</sup><span class="ltx_tag ltx_tag_note">1</span>Note body here</span></span></span> continues.</p></article>"""
    blocks, _ = extract_blocks(html)
    kinds = {b.kind: b.text.strip() for b in blocks}
    assert normalize(kinds["paragraph"]) == normalize("Main text continues.")
    assert normalize(kinds["footnote"]) == normalize("Note body here")
    assert check(html, "Main text[^1] continues.\n\n[^1]: Note body here\n").passed
    assert not check(html, "Main text[^1] continues.\n").passed


def test_count_tex_bibitems_from_eprint(tmp_path):
    extract_eprint((FIX / "eprint.tar.gz").read_bytes(), tmp_path / "src")
    assert count_tex_bibitems(tmp_path / "src") == 84
    assert count_tex_bibitems(tmp_path / "nowhere") is None
