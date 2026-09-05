"""Seam: ``frontmatter`` build/dump/parse round trip and the fingerprint."""

from pathlib import Path

from savepaper.arxiv import Resolved, parse_atom
from savepaper.frontmatter import build_source_frontmatter, dump, fingerprint, parse, update

FIX = Path(__file__).parent / "fixtures" / "atom"


def resolved():
    meta = parse_atom((FIX / "2503.17523.xml").read_bytes())[0]
    return Resolved(id=meta.id, version=meta.version, meta=meta)


def test_source_frontmatter_has_okf_fields_and_round_trips():
    fm = build_source_frontmatter(
        resolved(),
        sources=[{"id": "arxiv-html", "resource": "https://arxiv.org/html/2503.17523v3", "sha256": "abc"}],
        conversion={"route": "html", "converter": "pandoc 3.10; latexml-adapter 0.1", "coverage": 1.0, "known_losses": [], "fingerprint": "f00"},
        verified=True,
        generated_at="2026-09-05T13:40:00Z",
    )
    assert fm["type"] == "Paper"
    assert fm["title"].startswith("Bayesian Teaching")
    assert fm["description"].startswith("Large language models (LLMs)")
    assert fm["resource"] == "https://arxiv.org/abs/2503.17523v3"
    assert fm["arxiv"] == {
        "id": "2503.17523",
        "version": 3,
        "published": "2025-03-21",
        "updated": "2026-01-15",
        "categories": ["cs.CL", "cs.AI"],
        "doi": "10.1038/s41467-025-67998-6",
        "comment": "Nature Communications",
    }
    assert fm["authors"][0] == "Linlu Qiu" and len(fm["authors"]) == 6
    assert fm["tags"] == ["cs.CL", "cs.AI"]
    assert fm["generated"] == {"by": "save-paper/0.1", "at": "2026-09-05T13:40:00Z"}
    assert fm["verified"]["by"] == "process:save-paper-check"
    text = dump(fm, "# Title\n\nbody\n")
    assert text.startswith("---\ntype: Paper\n")
    back, body = parse(text)
    assert back == fm
    assert body == "# Title\n\nbody\n"


def test_unverified_frontmatter_has_no_verified_key():
    fm = build_source_frontmatter(resolved(), sources=[], conversion={"route": "pdf"}, verified=False)
    assert "verified" not in fm
    assert "figures_described" not in fm


def test_fingerprint_changes_with_any_input():
    base = fingerprint("2503.17523", 3, "sha", "pandoc 3.10", {"assets": True})
    assert base == fingerprint("2503.17523", 3, "sha", "pandoc 3.10", {"assets": True})
    assert base != fingerprint("2503.17523", 2, "sha", "pandoc 3.10", {"assets": True})
    assert base != fingerprint("2503.17523", 3, "sha2", "pandoc 3.10", {"assets": True})
    assert base != fingerprint("2503.17523", 3, "sha", "pandoc 3.11", {"assets": True})
    assert base != fingerprint("2503.17523", 3, "sha", "pandoc 3.10", {"assets": False})
    assert len(base) == 16


def test_parse_without_frontmatter_and_update_keeps_body():
    assert parse("plain\n") == ({}, "plain\n")
    text = dump({"type": "Paper", "title": "T"}, "body *here*\n")
    updated = update(text, figures_described={"count": 3})
    fm, body = parse(updated)
    assert fm["figures_described"] == {"count": 3} and fm["title"] == "T"
    assert body == "body *here*\n"


def test_long_description_is_not_folded():
    fm = {"type": "Paper", "description": "word " * 200}
    text = dump(fm, "")
    assert text.count("\n") < 8
    assert parse(text)[0]["description"] == fm["description"]
