"""Seam: ``savepaper.assets`` -- e-print bytes -> safe extraction -> local PNGs for each Figure.

External boundaries mocked: the network (``fetch_remote`` callable) and, for
the missing-tool case, ``shutil.which``. ``pdftoppm`` itself is exercised for
real because its output is the thing under test.
"""

import io
import shutil
import tarfile
from pathlib import Path

import pytest

from savepaper import assets
from savepaper.assets import extract_eprint, is_image, materialize, pdf_to_png
from savepaper.errors import ConvertError
from savepaper.latexml import Figure

FIX = Path(__file__).parent / "fixtures" / "2503.17523v3"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_tar(members: dict[str, bytes], extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra:
            extra(tar)
    return buf.getvalue()


@pytest.fixture
def two_figure_tar():
    return make_tar(
        {
            "main.tex": b"\\documentclass{article}\\begin{document}x\\end{document}",
            "figures/fig1_task.pdf": (FIX / "figures" / "fig1_task.pdf").read_bytes(),
            "figures/fig2_eval_results.pdf": (FIX / "figures" / "fig2_eval_results.pdf").read_bytes(),
        }
    )


# --- extraction ------------------------------------------------------------------


def test_extract_tar_gz_lists_members(tmp_path, two_figure_tar):
    result = extract_eprint(two_figure_tar, tmp_path / "src")
    assert result.kind == "tar"
    assert sorted(result.files) == ["figures/fig1_task.pdf", "figures/fig2_eval_results.pdf", "main.tex"]
    assert (tmp_path / "src" / "figures" / "fig1_task.pdf").stat().st_size > 1000
    assert result.rejected == []


def test_extract_rejects_path_escape_and_links(tmp_path):
    def add_bad(tar):
        link = tarfile.TarInfo("figures/link.pdf")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)

    data = make_tar({"../evil.txt": b"x", "/abs.txt": b"y", "ok.tex": b"z"}, extra=add_bad)
    result = extract_eprint(data, tmp_path / "src")
    assert result.files == ["ok.tex"]
    assert sorted(r["name"] for r in result.rejected) == ["../evil.txt", "/abs.txt", "figures/link.pdf"]
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "src" / "figures" / "link.pdf").exists()


def test_extract_rejects_oversized_member(tmp_path):
    data = make_tar({"big.bin": b"0" * 2048, "small.tex": b"a"})
    result = extract_eprint(data, tmp_path / "src", max_file_bytes=1024)
    assert result.files == ["small.tex"]
    assert result.rejected[0]["name"] == "big.bin"
    assert "size" in result.rejected[0]["reason"]


def test_extract_stops_after_member_cap(tmp_path):
    data = make_tar({f"f{i}.tex": b"x" for i in range(5)})
    result = extract_eprint(data, tmp_path / "src", max_members=3)
    assert len(result.files) == 3
    assert "members" in result.rejected[0]["reason"]


def test_extract_single_gzipped_tex_respects_cap(tmp_path):
    import gzip

    result = extract_eprint(gzip.compress(b"x" * 4096), tmp_path / "src", max_file_bytes=1024)
    assert result.files == []
    assert result.rejected[0]["reason"].startswith("size")


def test_extract_refuses_symlinked_destination_component(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "figures").symlink_to(outside)
    result = extract_eprint(make_tar({"figures/plot.pdf": b"%PDF"}), src)
    assert result.files == []
    assert result.rejected[0]["reason"] == "resolves outside destination"
    assert not (outside / "plot.pdf").exists()


def test_extract_single_gzipped_tex(tmp_path):
    import gzip

    data = gzip.compress(b"\\documentclass{article}")
    result = extract_eprint(data, tmp_path / "src")
    assert result.kind == "single"
    assert result.files == ["main.tex"]


def test_extract_pdf_only_eprint(tmp_path):
    result = extract_eprint(b"%PDF-1.7\n...", tmp_path / "src")
    assert result.kind == "pdf"
    assert result.files == []


# --- rendering --------------------------------------------------------------------


def test_find_source_prefers_exact_relative_path_and_refuses_ambiguous_basename(tmp_path):
    from savepaper.assets import find_source

    for rel in ["old/plot.pdf", "new/plot.png", "only/one.pdf", "a/dup.pdf", "b/dup.pdf"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    assert find_source(tmp_path, "new/plot.png") == tmp_path / "new/plot.png"
    assert find_source(tmp_path, "old/plot.svg") == tmp_path / "old/plot.pdf"
    assert find_source(tmp_path, "one.svg") == tmp_path / "only/one.pdf"  # unique basename anywhere
    assert find_source(tmp_path, "dup.svg") is None  # two candidates in different dirs: do not guess


def test_pdf_to_png_renders_a_real_png(tmp_path):
    out = tmp_path / "fig1_task.png"
    pdf_to_png(FIX / "figures" / "fig1_task.pdf", out)
    assert out.read_bytes()[:8] == PNG_MAGIC
    assert out.stat().st_size > 10_000


def test_pdf_to_png_without_pdftoppm_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ConvertError) as exc:
        pdf_to_png(FIX / "figures" / "fig1_task.pdf", tmp_path / "x.png")
    assert "pdftoppm" in str(exc.value)
    assert "doctor" in str(exc.value)


def test_is_image_uses_magic_bytes(tmp_path):
    assert is_image(PNG_MAGIC + b"rest") == "png"
    assert is_image(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert is_image(b"%PDF-1.4") == "pdf"
    assert is_image(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">') == "svg"
    assert is_image(b"<svg xmlns='http://www.w3.org/2000/svg'>") == "svg"
    assert is_image(b"hello") is None


# --- materialize ------------------------------------------------------------------


def figs():
    return [
        Figure(id="S2.F1.g1", kind="img", remote="2503.17523v3/fig1_task.png", local="images/2503.17523v3/fig1_task.png"),
        Figure(id="S2.F2.g1", kind="object", remote="2503.17523v3/fig2_eval_results.svg", local="images/2503.17523v3/fig2_eval_results.png"),
        Figure(id="S9.F9.g1", kind="object", remote="2503.17523v3/nowhere.svg", local="images/2503.17523v3/nowhere.png"),
    ]


def test_materialize_prefers_tarball_pdf_then_remote_png_then_svg_fallback(tmp_path, two_figure_tar):
    extract_eprint(two_figure_tar, tmp_path / "src")
    fetched = []

    def fetch_remote(remote):
        fetched.append(remote)
        if remote.endswith("fig1_task.png"):
            return (FIX / "fig1_task.png").read_bytes()
        if remote.endswith("nowhere.svg"):
            return b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        return None

    results = materialize(figs(), tmp_path / "src", tmp_path / "out", fetch_remote)
    by_id = {r.figure.id: r for r in results}
    # an ``img`` whose original PDF is in the e-print is rendered from it, not downloaded
    assert by_id["S2.F1.g1"].status == "rendered"
    assert (tmp_path / "out" / "images/2503.17523v3/fig1_task.png").read_bytes()[:8] == PNG_MAGIC
    assert by_id["S2.F2.g1"].status == "rendered"
    assert (tmp_path / "out" / "images/2503.17523v3/fig2_eval_results.png").read_bytes()[:8] == PNG_MAGIC
    assert by_id["S9.F9.g1"].status == "svg-fallback"
    assert by_id["S9.F9.g1"].path.name == "nowhere.svg"
    # only the graphic without a tarball original hits the network
    assert fetched == ["2503.17523v3/nowhere.svg"]
    mapping = assets.path_mapping(results)
    assert mapping == {"images/2503.17523v3/nowhere.png": "images/2503.17523v3/nowhere.svg"}


def test_materialize_fetches_remote_png_when_eprint_has_no_original(tmp_path):
    (tmp_path / "src").mkdir()
    results = materialize(figs()[:1], tmp_path / "src", tmp_path / "out", lambda remote: (FIX / "fig1_task.png").read_bytes())
    assert results[0].status == "remote"
    assert results[0].path.read_bytes()[:8] == PNG_MAGIC


def test_materialize_falls_back_to_remote_when_pdf_render_fails(tmp_path, monkeypatch):
    (tmp_path / "src" / "figures").mkdir(parents=True)
    (tmp_path / "src" / "figures" / "fig2_eval_results.pdf").write_bytes(b"%PDF-1.4 broken")
    fetched = []

    def fetch_remote(remote):
        fetched.append(remote)
        return b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"

    results = materialize(figs()[1:2], tmp_path / "src", tmp_path / "out", fetch_remote)
    assert results[0].status == "svg-fallback"
    assert fetched == ["2503.17523v3/fig2_eval_results.svg"]
    assert "pdftoppm failed" in results[0].note


def test_materialize_writes_inline_picture_svg(tmp_path):
    fig = Figure(id="S1.pic1", kind="picture", remote="", local="images/x/S1.pic1.svg", inline_svg="<svg xmlns='http://www.w3.org/2000/svg'><text>hi</text></svg>")
    results = materialize([fig], tmp_path / "src", tmp_path / "out", lambda remote: None)
    assert results[0].status == "inline-svg"
    assert (tmp_path / "out" / "images/x/S1.pic1.svg").read_text().startswith("<svg")


def test_materialize_marks_missing_when_nothing_is_available(tmp_path):
    (tmp_path / "src").mkdir()
    results = materialize(figs()[2:], tmp_path / "src", tmp_path / "out", lambda remote: None)
    assert results[0].status == "missing"
    assert results[0].path is None


def test_materialize_rejects_remote_bytes_that_are_not_images(tmp_path):
    (tmp_path / "src").mkdir()
    results = materialize(figs()[:1], tmp_path / "src", tmp_path / "out", lambda remote: b"<html>404</html>")
    assert results[0].status == "missing"
    assert "not an image" in results[0].note
