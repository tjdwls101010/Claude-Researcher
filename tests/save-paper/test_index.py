"""Seam: ``index.build_index(papers_dir)`` -> README.md text, regenerated from frontmatter only."""

from pathlib import Path

from savepaper.frontmatter import dump
from savepaper.index import build_index, write_index

EXPECTED = (Path(__file__).parent / "fixtures" / "index_expected.md").read_text(encoding="utf-8")


def make_papers(tmp_path):
    papers = tmp_path / "papers"
    (papers / "sources").mkdir(parents=True)
    (papers / "sources" / "2503.17523.md").write_text(
        dump(
            {
                "type": "Paper",
                "title": "Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models",
                "description": "Large language models (LLMs) are increasingly used as agents that interact with users and with the world. To do so successfully, LLMs must construct representations.",
                "arxiv": {"id": "2503.17523", "version": 3, "published": "2025-03-21"},
                "verified": {"by": "process:save-paper-check", "at": "2026-09-05T00:00:00Z"},
                "conversion": {"route": "html"},
            },
            "# body",
        )
    )
    (papers / "sources" / "2607.05775.md").write_text(
        dump(
            {
                "type": "Paper",
                "title": "A PDF-only Paper",
                "description": "Short abstract here.",
                "arxiv": {"id": "2607.05775", "version": 1, "published": "2026-07-08"},
                "conversion": {"route": "pdf", "known_losses": ["math", "figures"]},
            },
            "# body",
        )
    )
    (papers / "2503.17523.md").write_text(
        dump(
            {
                "type": "Paper Note",
                "title": "베이지안 티칭으로 LLM에 확률적 추론을 가르치기",
                "description": "LLM이 베이즈 추론을 모방하도록 파인튜닝하면 새로운 과제로도 일반화된다.",
                "sources": [{"id": "paper", "resource": "/papers/sources/2503.17523.md"}],
                "status": "draft",
                "verified": {"by": "human:seongjin", "at": "2026-09-06T00:00:00Z"},
            },
            "# 🖇️노트",
        )
    )
    (papers / "README.md").write_text("stale hand edits\n")
    return papers


def test_index_matches_fixture(tmp_path):
    papers = make_papers(tmp_path)
    assert build_index(papers) == EXPECTED


def test_write_index_overwrites_hand_edits(tmp_path):
    papers = make_papers(tmp_path)
    write_index(papers)
    text = (papers / "README.md").read_text()
    assert "stale hand edits" not in text
    assert text == EXPECTED


def test_empty_papers_dir(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    text = build_index(papers)
    assert "_No notes yet._" in text and "_No sources yet._" in text
