"""Seam: ``verify.numbers`` / ``verify.citations`` / ``verify.figures`` / ``verify.material_gaps`` on files the test writes; ``verify.verify_paper`` composes them."""

import json
from pathlib import Path

import pytest

from research import project, verify

NOW = "2026-09-07T01:00:00Z"


@pytest.fixture
def lay(tmp_path):
    lay = project.init_project(tmp_path, "toy", question="q", now=NOW)
    (lay.paper / "sections").mkdir()
    return lay


def registry_with(lay, *ids, cls="confirmatory"):
    lay.registry_json.write_text(json.dumps({"entries": [{"id": i, "class": cls, "statistics": {"n": 3, "mean": "0.7", "std": "0.01", "min": "0.6", "max": "0.8"}} for i in ids]}))


def test_numbers_only_through_macros(lay):
    registry_with(lay, "r001/method/accuracy")
    f = lay.paper / "sections" / "results.tex"
    f.write_text(
        "% a comment with 99 numbers\n"
        r"Our method reaches \result{r001/method/accuracy}{mean}{2} (\result{r001/method/accuracy}{n}{0} seeds), " + "\n"
        r"beating the \nonresult{0.61}{reported in Smith et al. 2024, Table 3} baseline~\cite{smith2024}." + "\n"
        r"\begin{tabular}{lcc} \multicolumn{2}{c}{x} \\ \cmidrule(lr){1-2}" + "\n"
        r"Row & 0.83 & \result{r001/method/accuracy}{std}{3} \\ \end{tabular}" + "\n"
        r"\includegraphics[width=0.5\textwidth]{figures/acc.pdf} See Section~\ref{sec:3} and Figure~2." + "\n"
        r"Trained for 100 epochs.\vspace{2mm}" + "\n"
    )
    out = verify.numbers(lay, [f])
    locs = [(x["location"], x["message"]) for x in out if x["severity"] == "major"]
    assert [l for l, _ in locs] == ["paper/sections/results.tex:5", "paper/sections/results.tex:6", "paper/sections/results.tex:7"]
    assert "0.83" in locs[0][1] and "2" in locs[1][1] and "100" in locs[2][1]


def test_unknown_entry_and_exploratory_context(lay):
    registry_with(lay, "r001/method/accuracy", cls="exploratory")
    f = lay.paper / "sections" / "results.tex"
    f.write_text(r"\result{r001/method/accuracy}{mean}{2} and \result{r009/x/y}{mean}{2} and \result{r001/method/accuracy}{median}{2}" + "\n")
    out = verify.numbers(lay, [f])
    kinds = {(x["severity"], x["message"].split(":")[0]) for x in out}
    assert ("major", "unknown registry entry r009/x/y") in kinds
    assert any(x["severity"] == "major" and "median" in x["message"] for x in out)
    assert any(x["severity"] == "warning" and "exploratory" in x["message"] for x in out)


BIB = r"""
@inproceedings{vaswani2017,
  title = {Attention is all you need},
  author = {Vaswani, Ashish and others},
  year = {2017},
  eprint = {1706.03762},
  archivePrefix = {arXiv},
}
@article{pdfpaper,
  title = {A PDF only paper},
  journal = {arXiv preprint arXiv:2607.05775},
  year = 2026
}
@article{wrongver,
  title = {Versioned},
  url = {https://arxiv.org/abs/2608.07885v1},
  year = {2026},
}
@article{unsaved, title={Never saved}, note={arXiv:2501.00001}, year={2025}}
@book{knuth1984, title = {The {TeX}book}, author = {Knuth, Donald E.}, year = {1984}, publisher = {Addison-Wesley}}
@book{unverified, title = {No human saw this}, year = {1999}}
"""


def make_sources(root: Path):
    src = root / "papers" / "sources"
    src.mkdir(parents=True)
    (src / "1706.03762.md").write_text("---\ntype: Paper\narxiv: {id: '1706.03762', version: 7}\nverified: {by: process:save-paper-check, at: x}\nconversion: {route: html}\n---\n\nx")
    (src / "2607.05775.md").write_text("---\ntype: Paper\narxiv: {id: '2607.05775', version: 1}\nconversion: {route: pdf}\n---\n\nx")
    (src / "2608.07885.md").write_text("---\ntype: Paper\narxiv: {id: '2608.07885', version: 2}\nverified: {by: process:save-paper-check, at: x}\nconversion: {route: html}\n---\n\nx")


def test_citations_are_checked_against_sources_and_literature(lay, tmp_path):
    make_sources(tmp_path)
    (lay.paper / "refs.bib").write_text(BIB)
    (lay.paper / "main.tex").write_text("\\input{sections/intro}\n\\bibliography{refs}\n")
    (lay.paper / "sections" / "intro.tex").write_text(r"\citep{vaswani2017,pdfpaper} \citet{wrongver} \cite{unsaved} \nocite{knuth1984} \cite{unverified} \cite{missingkey}" + "\n")
    lay.literature_md.write_text("---\ntype: Literature\nentries:\n  - key: knuth1984\n    title: The TeXbook\n    verified: {by: human:seongjin, at: 2026-09-01T00:00:00Z}\n  - key: unverified\n    title: x\n---\n")
    out = verify.citations(lay, lay.paper / "main.tex", lay.paper / "refs.bib")
    by_key = {}
    for x in out:
        by_key.setdefault(x["key"], []).append((x["severity"], x["message"]))
    assert "vaswani2017" not in by_key and "knuth1984" not in by_key
    assert by_key["pdfpaper"][0][0] == "warning" and "pdf" in by_key["pdfpaper"][0][1]
    assert by_key["wrongver"][0][0] == "major" and "v1" in by_key["wrongver"][0][1] and "2" in by_key["wrongver"][0][1]
    assert by_key["unsaved"][0][0] == "major" and "save-paper" in by_key["unsaved"][0][1]
    assert by_key["unverified"][0][0] == "major" and "literature.md" in by_key["unverified"][0][1]
    assert by_key["missingkey"][0][0] == "major" and "refs.bib" in by_key["missingkey"][0][1]
    assert all(x["location"].startswith("paper/sections/intro.tex:1") for x in out if x["key"] != "knuth1984")


def test_bibtex_parser_handles_nesting_and_strings():
    entries = verify.parse_bibtex(r"""
@string{acl = "Assoc. for Comp. Ling."}
@InProceedings{a, title = {Braces {in} {{nested}} form}, booktitle = acl, pages = "1--2", year = 2020}
@misc{b,title={x},}
""")
    assert set(entries) == {"a", "b"}
    assert entries["a"]["title"] == "Braces {in} {{nested}} form" and entries["a"]["pages"] == "1--2" and entries["a"]["year"] == "2020"
    assert entries["a"]["_type"] == "inproceedings"


def test_figures_must_come_from_the_manifest_and_gaps_are_named(lay):
    (lay.paper / "figures").mkdir()
    (lay.paper / "figures" / "manifest.json").write_text(json.dumps({"figures": {"acc.pdf": {}}}))
    f = lay.paper / "sections" / "results.tex"
    f.write_text(r"\includegraphics[width=1in]{figures/acc.pdf} \includegraphics{figures/hand.png}" + "\n\nThe [MATERIAL GAP] here.\n")
    figs = verify.figures(lay, [f])
    assert len(figs) == 1 and "hand.png" in figs[0]["message"] and figs[0]["location"] == "paper/sections/results.tex:1"
    gaps = verify.material_gaps([f])
    assert gaps == [{"severity": "major", "message": "[MATERIAL GAP] left in the draft", "location": "paper/sections/results.tex:3"}]


def test_verify_paper_composes_and_raises(lay, tmp_path):
    make_sources(tmp_path)
    (lay.paper / "refs.bib").write_text(BIB)
    (lay.paper / "main.tex").write_text("\\input{sections/results}\n")
    (lay.paper / "sections" / "results.tex").write_text(r"We get 0.9 \cite{vaswani2017}." + "\n")
    with pytest.raises(__import__("research.errors", fromlist=["GateError"]).GateError) as exc:
        verify.verify_paper(lay, result_files=[lay.paper / "sections" / "results.tex"])
    assert exc.value.findings[0]["location"] == "paper/sections/results.tex:1"
    (lay.paper / "sections" / "results.tex").write_text(r"We cite \cite{vaswani2017}." + "\n")
    out = verify.verify_paper(lay, result_files=[lay.paper / "sections" / "results.tex"])
    assert out["status"] == "verified"
