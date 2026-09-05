"""Seam: ``latexml.adapt(html)`` -> clean HTML, then ``pandoc.html_to_markdown`` -> Markdown string.

Each fixture under ``fixtures/latexml/`` is one construct cut from a real arXiv
HTML page (LaTeXML oxide 0.7.6). Assertions are on the Markdown pandoc emits,
because that is what the source layer's reader sees.
"""

import re
from pathlib import Path

import pytest

from savepaper.latexml import adapt
from savepaper.pandoc import html_to_markdown

FIX = Path(__file__).parent / "fixtures" / "latexml"


def md_of(name, **kw):
    result = adapt((FIX / f"{name}.html").read_text(encoding="utf-8"), image_dir="images/2503.17523v3", **kw)
    return result, html_to_markdown(result.html)


RAW_TAG = re.compile(r"<(?!!)[a-zA-Z/][^>]*>")


def no_raw_html(md):
    # ``$...$`` may legitimately contain ``<`` as an operator; strip math before scanning.
    stripped = re.sub(r"\$\$?.*?\$\$?", "", md, flags=re.S)
    return not RAW_TAG.search(stripped)


# --- math --------------------------------------------------------------------


def test_inline_math_becomes_dollar_tex_without_escapes():
    _, md = md_of("math")
    assert "$1+1=\\ldots$" in md
    assert "\\$" not in md  # the escaped form measured when MathML was pre-substituted


def test_equation_tables_become_display_math_with_tag():
    r, md = md_of("math")
    assert "$$\\displaystyle o^{*}(\\mathcal{O},\\bm{\\theta})=\\textrm{argmax}_{o\\in\\mathcal{O}}r(\\mathcal{O};\\bm{\\theta}).$$ (1)" in md
    assert "$$\\mathrm{Spec}\\;=\\;" in md  # 2608 table.ltx_equation
    assert "$$\\lim_{{t\\to 0}}" in md  # 2512 span.ltx_equation
    assert "|" not in md.replace("\\|", "").split("$$")[0]  # no pipe table produced for equations
    assert "|---" not in md


def test_unparsed_math_is_kept_and_counted():
    r, md = md_of("math")
    assert r.unparsed_math == 2
    assert "\\mathbbm{1}\\big[" in md


def test_math_output_has_no_raw_html():
    _, md = md_of("math")
    assert no_raw_html(md)


# --- figures -----------------------------------------------------------------


def test_object_svg_figure_requests_png_from_source_and_keeps_caption():
    r, md = md_of("figure_object")
    assert len(r.figures) == 1
    f = r.figures[0]
    assert f.kind == "object"
    assert f.remote == "2503.17523v3/fig2_eval_results.svg"
    assert f.local == "images/2503.17523v3/fig2_eval_results.png"
    assert "![](images/2503.17523v3/fig2_eval_results.png)" in md
    assert "*Figure 2: LLMs show limited or no improvement" in md


def test_img_figure_keeps_png_path():
    r, md = md_of("figure_img")
    assert r.figures[0].kind == "img"
    assert r.figures[0].local == "images/2503.17523v3/fig1_task.png"
    assert "![](images/2503.17523v3/fig1_task.png)" in md
    assert "Refer to caption" not in md


def test_figure_panels_keep_order_and_single_caption():
    r, md = md_of("figure_panels")
    assert [f.remote for f in r.figures] == ["2503.17523v3/fig5_gen_results.svg"]
    assert md.count("*Figure 5:") == 1
    assert md.index("![](") < md.index("*Figure 5:")


def test_figure_path_rewrite_for_svg_fallback():
    r, _ = md_of("figure_object")
    html = r.rewrite_figure_paths({r.figures[0].local: "images/2503.17523v3/fig2_eval_results.svg"})
    md = html_to_markdown(html)
    assert "![](images/2503.17523v3/fig2_eval_results.svg)" in md


# --- tables ------------------------------------------------------------------


def test_table_without_thead_promotes_first_row_and_keeps_all_cells():
    _, md = md_of("table_nohead")
    assert re.search(r"\| Product Category\s+\| User’s Goals \(Preferred Attributes\)\s+\|", md)
    for cell in ["eco friendly, twin with drawers", "memory foam, solid wood", "daily wear, color back, size 14", "Food & beverage"]:
        assert cell in md
    assert md.index("*Table 1: Example product categories") < md.index("| Product Category")
    assert re.search(r"^\|-+\|-+\|$", md, re.M)  # header separator right after the promoted row


def test_table_with_colspan_headers_keeps_every_cell():
    _, md = md_of("table_guessed")
    for cell in ["In-Domain Tasks", "Unseen Tasks", "gemma-3-27b-it", "39.8", "GPQA-D", "Avg."]:
        assert cell in md
    assert no_raw_html(md)


# --- text boxes, footnotes, listings, theorems --------------------------------


def test_foreignobject_text_is_recovered():
    r, md = md_of("foreignobject")
    assert "We call a subarray of an array complete if the number of distinct elements" in md
    assert "Here’s the plan: 1) Model 2 will develop an algorithm" in md
    assert "<svg" not in md and "<path" not in md
    assert r.figures == []


def test_footnotes_become_gfm_footnotes():
    r, md = md_of("footnote")
    assert r.counts["footnotes"] == 3
    assert re.search(r"the very best baseline[^\n]*\[\^1\]", md) or "[^1]" in md
    assert "[^1]: https://livecodebench.github.io/leaderboard.html" in md
    assert "[^3]: https://artificialanalysis.ai/evaluations/gpqa-diamond?models=o3%2Cgpt-5" in md
    body = md.split("[^1]:")[0]
    assert "https://livecodebench.github.io" not in body  # note body no longer inlined mid-sentence


def test_listing_lines_are_one_per_line_in_a_code_block_with_tex_math():
    r, md = md_of("listing")
    assert r.counts["listings"] == 1
    lines = md.splitlines()
    code = [l for l in lines if l.startswith("    ")]
    assert len(code) == 20
    assert code[0].strip().startswith("1: initial rubric $R_{0}$; train/val/test sets")
    assert "R0R_{0}" not in md  # MathML text must not leak next to the TeX
    assert "*Algorithm 1 Reasoning-Aligned Rubric Tuning*" in md  # caption, same shape as figures


def test_theorem_and_proof_become_blockquotes_with_bold_title():
    _, md = md_of("theorem")
    assert "> **Theorem 1.** For every $\\epsilon>0$ the bound holds." in md
    assert "> **Proof.** Immediate from the definition." in md


# --- front matter, bibliography, lists ----------------------------------------


def test_frontmatter_title_authors_abstract_and_numbered_section():
    r, md = md_of("frontmatter")
    assert r.title == "Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models"
    assert md.startswith("# Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models")
    assert "Linlu Qiu (MIT), Fei Sha (Meta), Kelsey Allen (Google DeepMind; University of British Columbia; Vector Institute)" in md
    assert "## Abstract" in md
    assert "## 1 Introduction" in md
    assert r.counts["sections"] == 1


def test_bibliography_is_one_item_per_line_without_cited_by_noise():
    r, md = md_of("bibliography")
    assert r.counts["bibitems"] == 21
    assert "## References" in md
    items = [l for l in md.splitlines() if l.startswith("- ")]
    assert len(items) == 21
    assert items[0].startswith("- Agrawal et al. (2026) L. A. Agrawal, S. Tan")
    assert "GEPA: reflective prompt evolution can outperform reinforcement learning" in items[0]
    assert "Cited by" not in md
    assert "https://openreview.net/forum?id=RQm2KQTM5r" in md  # external link kept


def test_lists_do_not_duplicate_markers_and_quote_kept():
    _, md = md_of("lists")
    assert "- • " not in md
    assert re.search(r"^- We introduce the RL Conductor", md, re.M)
    assert re.search(r"^1\.\s+A lifecycle view of LLM judges", md, re.M)
    assert not re.search(r"^1\.\s+1\.", md, re.M)
    assert "> **Definition.** Each agentic workflow" in md


def test_refs_and_cites_keep_text_only():
    _, md = md_of("footnote")
    assert "as shown in Table 1" in md
    assert "](#" not in md


def test_drawn_picture_becomes_an_inline_svg_asset():
    html = """<article class="ltx_document"><div class="ltx_para"><p class="ltx_p">See the diagram.</p>
    <svg class="ltx_picture" id="S1.pic1" viewBox="0 0 10 10"><g><path d="M0 0L10 10"/><text>A</text></g></svg></div></article>"""
    r = adapt(html, image_dir="images/x")
    assert len(r.figures) == 1 and r.figures[0].kind == "picture"
    assert r.figures[0].local == "images/x/S1.pic1.svg"
    assert r.figures[0].inline_svg.startswith("<svg")
    assert "![](images/x/S1.pic1.svg)" in html_to_markdown(r.html)


def test_local_names_come_from_the_eprint_relative_path():
    from savepaper.latexml import Figure, local_name

    assert local_name("fig1_task.png") == "fig1_task"
    assert local_name("figs/leaderboard_vert2.png") == "figs_leaderboard_vert2"
    f = Figure(id="", kind="img", remote="2512.04388v5/figs/leaderboard_vert2.png", local="")
    assert f.source_relpath == "figs/leaderboard_vert2.png"


def test_missing_document_root_raises():
    from savepaper.errors import ConvertError

    with pytest.raises(ConvertError):
        adapt("<html><body><p>not latexml</p></body></html>", image_dir="images/x")
