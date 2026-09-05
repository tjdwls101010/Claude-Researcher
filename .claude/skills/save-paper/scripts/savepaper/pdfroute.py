"""PDF fallback via ``npx -y @firecrawl/anydoc``. Text and tables survive; most math and all figures do not.

Only used when arXiv has no HTML for the paper (the author uploaded a PDF, or
LaTeXML failed). The result is saved with ``conversion.route: pdf`` and
``known_losses: [math, figures]`` and is never marked ``verified`` -- there is
no DOM to check it against.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .errors import ConvertError

ANYDOC_CMD = ["npx", "-y", "@firecrawl/anydoc"]
KNOWN_LOSSES = ["math", "figures"]


def anydoc_available() -> bool:
    return shutil.which("npx") is not None


def pdf_to_markdown(pdf: Path, runner: Callable = subprocess.run, timeout: int = 600) -> str:
    if not anydoc_available():
        raise ConvertError("npx not found; anydoc needs Node.js (run `save_paper.py doctor`)")
    proc = runner([*ANYDOC_CMD, str(pdf)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ConvertError(f"anydoc failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    md = proc.stdout
    if len(md.strip()) < 200:
        raise ConvertError("anydoc produced almost no text; the PDF is probably scanned (needs OCR, out of scope)")
    return md
