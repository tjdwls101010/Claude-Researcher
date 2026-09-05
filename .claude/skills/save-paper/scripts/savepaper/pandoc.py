"""pandoc invocation with the writer flags fixed by measurement.

Why these flags: ``gfm`` so tables become pipe tables and footnotes ``[^n]``;
``-raw_html`` so nothing leaks as HTML tags; ``-tex_math_gfm+tex_math_dollars``
so MathML ``<annotation encoding="application/x-tex">`` comes out as ``$..$``
instead of the escaped ``\\$\\pi\\_{\\theta}\\$`` you get by pre-substituting
text (plan session, 2026-09-05). ``--wrap=none`` keeps one paragraph per line so
the coverage check and Edit tools see the text as it is.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache

from .errors import ConvertError

WRITER = "gfm-raw_html-tex_math_gfm+tex_math_dollars"
READER = "html"


def pandoc_path() -> str | None:
    return shutil.which("pandoc")


@lru_cache(maxsize=1)
def pandoc_version() -> str:
    exe = pandoc_path()
    if not exe:
        raise ConvertError("pandoc not found on PATH (run `save_paper.py doctor`)")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True).stdout
    return out.splitlines()[0].strip()  # "pandoc 3.10"


def html_to_markdown(html: str) -> str:
    exe = pandoc_path()
    if not exe:
        raise ConvertError("pandoc not found on PATH (run `save_paper.py doctor`)")
    proc = subprocess.run(
        [exe, "-f", READER, "-t", WRITER, "--wrap=none"],
        input=html.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ConvertError(f"pandoc failed ({proc.returncode}): {proc.stderr.decode('utf-8', 'replace')[:500]}")
    return proc.stdout.decode("utf-8")
