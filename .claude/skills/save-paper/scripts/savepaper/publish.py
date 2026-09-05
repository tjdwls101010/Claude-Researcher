"""Staging directory -> atomic publish into ``papers/sources/``.

Everything is built and checked under ``papers/sources/.staging/<id>v<n>/`` and
moved into place with ``os.replace`` at the very end, so an interrupted run or a
failed batch member leaves no half-written ``<id>.md`` behind. The index is
regenerated only after a publish succeeds (that is the caller's job).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .frontmatter import parse


@dataclass
class Layout:
    papers_dir: Path

    @property
    def sources_dir(self) -> Path:
        return self.papers_dir / "sources"

    @property
    def images_dir(self) -> Path:
        return self.sources_dir / "images"

    def source_md(self, safe_id: str) -> Path:
        return self.sources_dir / f"{safe_id}.md"

    def note_md(self, safe_id: str) -> Path:
        return self.papers_dir / f"{safe_id}.md"

    def image_dir(self, vid: str) -> Path:
        return self.images_dir / vid

    def staging(self, vid: str) -> Path:
        return self.sources_dir / ".staging" / vid

    @property
    def index_md(self) -> Path:
        return self.papers_dir / "README.md"


def existing_fingerprint(md_path: Path) -> Optional[str]:
    if not md_path.exists():
        return None
    fm, _ = parse(md_path.read_text(encoding="utf-8"))
    conv = fm.get("conversion") or {}
    return conv.get("fingerprint")


def existing_version(md_path: Path) -> Optional[int]:
    if not md_path.exists():
        return None
    fm, _ = parse(md_path.read_text(encoding="utf-8"))
    return (fm.get("arxiv") or {}).get("version")


class Staging:
    """``with Staging(layout, vid) as st:`` -- a fresh directory that is removed on exit unless published."""

    def __init__(self, layout: Layout, vid: str):
        self.layout = layout
        self.vid = vid
        self.dir = layout.staging(vid)
        self.published = False

    def __enter__(self) -> "Staging":
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)
        return self

    def __exit__(self, *exc) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        staging_root = self.dir.parent
        if staging_root.exists() and not any(staging_root.iterdir()):
            staging_root.rmdir()

    @property
    def md(self) -> Path:
        return self.dir / "paper.md"

    @property
    def images(self) -> Path:
        """Figures are staged at the same relative depth as in the final tree so the Markdown paths are identical."""
        return self.dir / "images" / self.vid

    @property
    def sources(self) -> Path:
        return self.dir / "src"

    def publish(self, safe_id: str) -> Path:
        """Move the Markdown and the image directory into place. Old images for other versions are removed."""
        final_md = self.layout.source_md(safe_id)
        final_md.parent.mkdir(parents=True, exist_ok=True)
        if self.images.exists():
            self.layout.images_dir.mkdir(parents=True, exist_ok=True)
            final_img = self.layout.image_dir(self.vid)
            if final_img.exists():
                shutil.rmtree(final_img)
            os.replace(self.images, final_img)
            arxiv_id = self.vid.rsplit("v", 1)[0]
            for old in self.layout.images_dir.glob(f"{arxiv_id}v*"):
                if old.name != self.vid and old.is_dir():
                    shutil.rmtree(old)
        os.replace(self.md, final_md)
        self.published = True
        return final_md
