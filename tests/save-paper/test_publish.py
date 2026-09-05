"""Seam: ``publish.Staging`` -- nothing reaches ``papers/sources`` until ``publish`` is called."""

from pathlib import Path

import pytest

from savepaper.frontmatter import dump
from savepaper.publish import Layout, Staging, existing_fingerprint, existing_version


def test_failure_inside_staging_leaves_no_trace(tmp_path):
    layout = Layout(tmp_path / "papers")
    with pytest.raises(RuntimeError):
        with Staging(layout, "2503.17523v3") as st:
            st.md.write_text("half written")
            st.images.mkdir(parents=True)
            (st.images / "a.png").write_bytes(b"x")
            raise RuntimeError("boom")
    assert not layout.source_md("2503.17523").exists()
    assert not (layout.sources_dir / ".staging").exists()
    assert list(layout.sources_dir.rglob("*")) == [] if layout.sources_dir.exists() else True


def test_publish_moves_markdown_and_images_and_drops_older_version_images(tmp_path):
    layout = Layout(tmp_path / "papers")
    old_img = layout.image_dir("2503.17523v2")
    old_img.mkdir(parents=True)
    (old_img / "old.png").write_bytes(b"o")
    with Staging(layout, "2503.17523v3") as st:
        st.md.write_text(dump({"type": "Paper", "arxiv": {"id": "2503.17523", "version": 3}, "conversion": {"fingerprint": "abc"}}, "body"))
        st.images.mkdir(parents=True)
        (st.images / "fig1.png").write_bytes(b"png")
        final = st.publish("2503.17523")
    assert final == layout.source_md("2503.17523")
    assert final.read_text().endswith("body")
    assert (layout.image_dir("2503.17523v3") / "fig1.png").read_bytes() == b"png"
    assert not old_img.exists()
    assert not (layout.sources_dir / ".staging").exists()
    assert existing_fingerprint(final) == "abc"
    assert existing_version(final) == 3
    assert existing_fingerprint(layout.source_md("nope")) is None


def test_republish_replaces_previous_image_dir(tmp_path):
    layout = Layout(tmp_path / "papers")
    for content in (b"first", b"second"):
        with Staging(layout, "1.1v1") as st:
            st.md.write_text("x")
            st.images.mkdir(parents=True)
            (st.images / "f.png").write_bytes(content)
            st.publish("1.1")
    assert (layout.image_dir("1.1v1") / "f.png").read_bytes() == b"second"
