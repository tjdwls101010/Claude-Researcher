"""Figure assets: safe e-print extraction, PDF -> PNG, and the per-figure decision.

Why the tarball at all: LaTeXML turns an author's PDF figure into ``<object
data=*.svg>``; rasterising that SVG with ImageMagick's built-in renderer smears
bar charts black (measured), while ``pdftoppm -png -r 110`` on the original PDF
from the e-print is exact. So for ``object`` graphics we look in the tarball
first and only fall back to fetching arXiv's SVG when no original exists.

Extraction is treated as untrusted input: no absolute paths, no ``..``, no
links or devices, and size caps.
"""

from __future__ import annotations

import gzip
import io
import shutil
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from .errors import ConvertError
from .latexml import Figure

# 성진: 50MB/file, 500MB/tarball is a guess at "no real paper exceeds this";
# raise both if a legitimate e-print gets rejected (the rejection lists the size).
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_MEMBERS = 10_000  # e-prints have tens to hundreds of files; this only stops a hostile archive
PNG_DPI = 110  # measured: crisp at reading size, ~100-300 KB per figure

_SOURCE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".eps")


@dataclass
class ExtractResult:
    kind: str  # "tar" | "single" | "pdf" | "unknown"
    files: list[str] = field(default_factory=list)  # relative paths actually written
    rejected: list[dict] = field(default_factory=list)  # {name, reason}


@dataclass
class AssetResult:
    figure: Figure
    status: str  # "rendered" | "copied" | "remote" | "svg-fallback" | "inline-svg" | "missing"
    path: Optional[Path]  # absolute path written, or None
    note: str = ""

    @property
    def actual_local(self) -> str:
        """Markdown-relative path that really exists (may differ from figure.local on fallback)."""
        if self.path is None:
            return self.figure.local
        return str(PurePosixPath(self.figure.local).with_suffix(self.path.suffix))


def is_image(data: bytes) -> Optional[str]:
    """Type from magic bytes: png | jpeg | gif | pdf | svg, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"GIF8"):
        return "gif"
    if data.startswith(b"%PDF"):
        return "pdf"
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "svg"
    return None


def _safe_member(info: tarfile.TarInfo, max_file_bytes: int) -> Optional[str]:
    """Return a rejection reason, or None if the member may be written."""
    p = PurePosixPath(info.name)
    if p.is_absolute() or info.name.startswith(("/", "\\")):
        return "absolute path"
    if ".." in p.parts:
        return "path escapes destination"
    if info.issym() or info.islnk():
        return "link"
    if not (info.isfile() or info.isdir()):
        return f"unsupported member type {info.type!r}"
    if info.size > max_file_bytes:
        return f"size {info.size} exceeds cap {max_file_bytes}"
    return None


def extract_eprint(
    data: bytes,
    dest: Path,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_members: int = MAX_MEMBERS,
) -> ExtractResult:
    """Unpack an arXiv e-print (gzipped tar, gzipped single file, or bare PDF) into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    if data.startswith(b"%PDF"):
        return ExtractResult(kind="pdf")
    if not data.startswith(b"\x1f\x8b"):
        return ExtractResult(kind="unknown")
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError:
        # gzipped single file: arXiv serves a lone .tex this way. Bound the inflated size too.
        cap = min(max_file_bytes, max_total_bytes)
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            payload = gz.read(cap + 1)
        if len(payload) > cap:
            return ExtractResult(kind="single", rejected=[{"name": "main.tex", "reason": f"size exceeds cap {cap}"}])
        (dest / "main.tex").write_bytes(payload)
        return ExtractResult(kind="single", files=["main.tex"])
    result = ExtractResult(kind="tar")
    total = 0
    root = dest.resolve()
    with tar:
        for n, info in enumerate(tar):
            if n >= max_members:
                result.rejected.append({"name": info.name, "reason": f"more than {max_members} members; rest skipped"})
                break
            reason = _safe_member(info, max_file_bytes)
            if reason is None and total + info.size > max_total_bytes:
                reason = f"total size would exceed cap {max_total_bytes}"
            if reason:
                result.rejected.append({"name": info.name, "reason": reason})
                continue
            target = dest / info.name
            # Containment re-checked on the resolved path: a symlinked directory already under
            # dest (from a reused staging dir) would otherwise redirect the write.
            if not target.resolve().is_relative_to(root) or target.is_symlink():
                result.rejected.append({"name": info.name, "reason": "resolves outside destination"})
                continue
            if info.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(info)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            total += info.size
            result.files.append(info.name)
    return result


def find_source(extracted_dir: Path, relpath: str) -> Optional[Path]:
    """Original graphic for ``relpath`` (e-print-relative, any suffix): exact path first,
    preferring PDF (exact) over rasters; a basename-only match is accepted only when it is
    unambiguous, so ``old/plot.pdf`` never stands in for ``new/plot.png`` (codex review)."""
    if not extracted_dir.exists():
        return None
    rel = PurePosixPath(relpath)

    def rank(p: Path):
        return (_SOURCE_SUFFIXES.index(p.suffix.lower()), len(p.parts))

    exact = [p for p in (extracted_dir / rel.parent).glob(rel.stem + ".*") if p.is_file() and p.suffix.lower() in _SOURCE_SUFFIXES]
    if exact:
        return min(exact, key=rank)
    loose = [p for p in extracted_dir.rglob(rel.stem + ".*") if p.is_file() and p.suffix.lower() in _SOURCE_SUFFIXES]
    dirs = {p.parent for p in loose}
    if len(dirs) == 1:
        return min(loose, key=rank)
    return None


def pdf_to_png(pdf: Path, out_png: Path, dpi: int = PNG_DPI) -> None:
    exe = shutil.which("pdftoppm")
    if not exe:
        raise ConvertError("pdftoppm (poppler) not found on PATH; run `save_paper.py doctor` for the install command")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = out_png.with_suffix("")
    proc = subprocess.run(
        [exe, "-png", "-r", str(dpi), "-singlefile", "-f", "1", "-l", "1", str(pdf), str(prefix)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out_png.exists():
        raise ConvertError(f"pdftoppm failed on {pdf.name}: {proc.stderr.strip()[:300]}")


def materialize(
    figures: list[Figure],
    extracted_dir: Path,
    out_root: Path,
    fetch_remote: Callable[[str], Optional[bytes]],
) -> list[AssetResult]:
    """Produce one local file per figure under ``out_root / figure.local``.

    Order of preference: tarball original (PDF rendered, raster copied) -> the file
    arXiv serves at ``remote`` (PNG for ``img``; SVG only as a last resort for
    ``object``, recorded so the caller can list it as a known loss) -> missing.
    """
    results: list[AssetResult] = []
    for fig in figures:
        target = out_root / fig.local
        if fig.kind == "picture":
            dest = target.with_suffix(".svg")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(fig.inline_svg or "", encoding="utf-8")
            results.append(AssetResult(fig, "inline-svg", dest, "LaTeXML-drawn picture kept as SVG markup"))
            continue
        relpath = fig.source_relpath
        source = find_source(extracted_dir, relpath)
        note = ""
        if source is not None and source.suffix.lower() == ".pdf":
            try:
                pdf_to_png(source, target)
                results.append(AssetResult(fig, "rendered", target, f"from {source.name}"))
                continue
            except ConvertError as exc:
                note = f"{exc}; "  # fall through to the remote copy rather than giving up
        elif source is not None and source.suffix.lower() in (".png", ".jpg", ".jpeg"):
            data = source.read_bytes()
            if is_image(data) in ("png", "jpeg"):
                dest = target.with_suffix(source.suffix.lower().replace(".jpeg", ".jpg"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                results.append(AssetResult(fig, "copied", dest, f"from {source.name}"))
                continue
        data = fetch_remote(fig.remote)
        if data:
            kind = is_image(data)
            if kind in ("png", "jpeg", "gif"):
                dest = target.with_suffix({"png": ".png", "jpeg": ".jpg", "gif": ".gif"}[kind])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                results.append(AssetResult(fig, "remote", dest, note + "fetched from arxiv.org/html"))
                continue
            if kind == "svg":
                dest = target.with_suffix(".svg")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                results.append(AssetResult(fig, "svg-fallback", dest, note + "no usable original in e-print; arXiv SVG kept as-is"))
                continue
            results.append(AssetResult(fig, "missing", None, note + f"remote {fig.remote} is not an image"))
            continue
        results.append(AssetResult(fig, "missing", None, note + f"no source for {relpath} in e-print and nothing at {fig.remote}"))
    return results


def path_mapping(results: list[AssetResult]) -> dict[str, str]:
    """``figure.local -> actual_local`` for every figure whose file ended up with another suffix."""
    return {r.figure.local: r.actual_local for r in results if r.path is not None and r.actual_local != r.figure.local}
