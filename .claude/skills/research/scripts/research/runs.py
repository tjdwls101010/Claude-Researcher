"""Experiment runs: start one locally (no shell), import one from elsewhere against a manifest, seal what completed.

A run directory is written only by this module. The seal is sha256 of every
file at completion; the registry trusts a run only while the seal still
matches, so a hand edit after the fact excludes the run instead of changing a
number silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import GateError, InputError, NotFoundError, SubprocessError
from .project import Layout, now_iso, write_readme

ENV_ALLOWLIST = frozenset({"RESEARCH_RUN_DIR", "RESEARCH_SEEDS", "RESEARCH_RUN_ID", "PYTHONHASHSEED", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "PYTORCH_ENABLE_MPS_FALLBACK", "TOKENIZERS_PARALLELISM"})
SEALED_FILES = ("run.json", "results.json", "output.log")
_RID = re.compile(r"r\d{3,}")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SCHEMA_VERSION = 1
DIRECTIONS = ("maximize", "minimize")


# --- results.json -----------------------------------------------------------------


def validate_results(obj) -> list[dict]:
    """Findings against the results contract; empty means valid. Values must be decimal strings so rounding is explicit."""
    f = []
    if not isinstance(obj, dict):
        return [{"severity": "major", "message": "results.json must be an object", "location": "results.json"}]
    if obj.get("schema_version") != SCHEMA_VERSION:
        f.append({"severity": "major", "message": f"schema_version must be {SCHEMA_VERSION}", "location": "results.json:schema_version"})
    md = obj.get("metric_def")
    if not isinstance(md, dict) or not md:
        f.append({"severity": "major", "message": "metric_def: object of {metric: {description, unit, direction}} required", "location": "results.json:metric_def"})
        md = {}
    for m, d in md.items():
        if "/" in m:
            f.append({"severity": "major", "message": f"metric name {m!r} must not contain '/' (it is a registry id separator)", "location": f"results.json:metric_def.{m}"})
        if not isinstance(d, dict) or not all(isinstance(d.get(k), str) and d.get(k) for k in ("description", "unit")) or d.get("direction") not in DIRECTIONS:
            f.append({"severity": "major", "message": f"metric_def.{m} needs description, unit and direction in {DIRECTIONS}", "location": f"results.json:metric_def.{m}"})
    conds = obj.get("conditions")
    if not isinstance(conds, dict) or not conds:
        f.append({"severity": "major", "message": "conditions: object of {condition: {config_sha256}} required", "location": "results.json:conditions"})
        conds = {}
    for c in conds:
        if "/" in c:
            f.append({"severity": "major", "message": f"condition name {c!r} must not contain '/'", "location": f"results.json:conditions.{c}"})
    obs = obj.get("observations")
    if not isinstance(obs, list) or not obs:
        f.append({"severity": "major", "message": "observations: non-empty list of {condition, seed, metrics} required", "location": "results.json:observations"})
        obs = []
    seen = set()
    for i, o in enumerate(obs):
        loc = f"results.json:observations[{i}]"
        if not isinstance(o, dict):
            f.append({"severity": "major", "message": "must be an object", "location": loc})
            continue
        if o.get("condition") not in conds:
            f.append({"severity": "major", "message": f"condition {o.get('condition')!r} is not declared in conditions", "location": loc})
        if not isinstance(o.get("seed"), int) or isinstance(o.get("seed"), bool):
            f.append({"severity": "major", "message": "seed must be an integer", "location": loc})
        key = (o.get("condition"), o.get("seed"))
        if key in seen:
            f.append({"severity": "major", "message": f"duplicate observation for {key}", "location": loc})
        seen.add(key)
        metrics = o.get("metrics")
        if not isinstance(metrics, dict) or not metrics:
            f.append({"severity": "major", "message": "metrics must be a non-empty object of decimal strings", "location": loc})
            continue
        for m, v in metrics.items():
            if not isinstance(v, str):
                f.append({"severity": "major", "message": f"metrics.{m} must be a decimal string (got {type(v).__name__}); strings keep rounding explicit", "location": loc})
    return f


# --- sealing -----------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sealed_files(run_dir: Path) -> list[str]:
    files = [f for f in SEALED_FILES if (run_dir / f).is_file()]
    art = run_dir / "artifacts"
    if art.is_dir():
        files += sorted(p.relative_to(run_dir).as_posix() for p in art.rglob("*") if p.is_file())
    return files


def seal(run_dir: Path, *, now: str | None = None) -> Path:
    files = {rel: _sha256(run_dir / rel) for rel in sealed_files(run_dir)}
    out = run_dir / "seal.json"
    out.write_text(json.dumps({"sealed_at": now or now_iso(), "files": files}, indent=1), encoding="utf-8")
    return out


def verify_seal(run_dir: Path) -> list[dict]:
    """Mismatches between seal.json and the files now; empty means intact. A missing seal is one finding."""
    sj = run_dir / "seal.json"
    if not sj.is_file():
        return [{"severity": "major", "message": "no seal.json (the run never completed, or was hand-made)", "location": "seal.json"}]
    try:
        files = json.loads(sj.read_text(encoding="utf-8"))["files"]
    except (ValueError, KeyError, TypeError):
        return [{"severity": "major", "message": "seal.json unreadable", "location": "seal.json"}]
    out = []
    for rel, sha in files.items():
        p = run_dir / rel
        if not p.is_file():
            out.append({"severity": "major", "message": "sealed file missing", "location": rel})
        elif _sha256(p) != sha:
            out.append({"severity": "major", "message": "content differs from the seal", "location": rel})
    for rel in sealed_files(run_dir):
        if rel not in files:
            out.append({"severity": "major", "message": "file added after the seal", "location": rel})
    return out


# --- allocation and metadata ----------------------------------------------------------


def next_run_id(lay: Layout) -> str:
    nums = [int(p.name[1:]) for p in lay.runs.glob("r*") if _RID.fullmatch(p.name) and p.is_dir()]
    return f"r{(max(nums) + 1 if nums else 1):03d}"


def _check_seeds(seeds) -> list[int]:
    if not isinstance(seeds, list) or not seeds:
        raise InputError("seeds: at least one integer seed is required")
    if any((not isinstance(s, int)) or isinstance(s, bool) for s in seeds):
        raise InputError("seeds: every seed must be an integer")
    if len(set(seeds)) != len(seeds):
        raise InputError("seeds: duplicates")
    return list(seeds)


def _check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise InputError("name: 1-64 chars of letters, digits, '.', '_' or '-'")
    return name


def git_state(root: Path, run=subprocess.run) -> tuple[str | None, str | None]:
    """(HEAD sha, sha256 of `git diff HEAD`) or (None, None) outside a repository; makes a run auditable after the fact."""
    try:
        head = run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=30)
        if head.returncode != 0:
            return None, None
        diff = run(["git", "diff", "HEAD", "--", "."], cwd=str(root), capture_output=True, timeout=60)
        return head.stdout.strip(), hashlib.sha256(diff.stdout if isinstance(diff.stdout, bytes) else diff.stdout.encode()).hexdigest()
    except (OSError, subprocess.SubprocessError):
        return None, None


def _write_run_json(run_dir: Path, data: dict) -> None:
    (run_dir / "run.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _finish_results(run_dir: Path, rj: dict, *, now: str | None) -> list[dict]:
    """Validate results.json; on success seal and mark completed, else mark failed. Returns findings."""
    rpath = run_dir / "results.json"
    if not rpath.is_file():
        rj["status"] = "failed"
        _write_run_json(run_dir, rj)
        return [{"severity": "major", "message": "the experiment exited 0 but wrote no results.json into $RESEARCH_RUN_DIR", "location": "results.json"}]
    try:
        findings = validate_results(json.loads(rpath.read_text(encoding="utf-8")))
    except ValueError as exc:
        findings = [{"severity": "major", "message": f"results.json is not JSON: {exc}", "location": "results.json"}]
    if findings:
        rj["status"] = "failed"
        _write_run_json(run_dir, rj)
        return findings
    rj["status"] = "completed"
    _write_run_json(run_dir, rj)
    seal(run_dir, now=now)
    return []


# --- start --------------------------------------------------------------------------


def start(
    lay: Layout,
    name: str,
    argv: list[str],
    *,
    seeds: list[int],
    confirmatory: bool = False,
    prereg: str | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    run=subprocess.run,
    now: str | None = None,
) -> dict:
    """Run ``argv`` once without a shell, with RESEARCH_RUN_DIR / RESEARCH_SEEDS / RESEARCH_RUN_ID injected, and seal the result.

    Failure, missing results and interruption are recorded in run.json (and raised); only a completed,
    valid run is sealed, and only sealed runs reach the registry.
    """
    _check_name(name)
    seeds = _check_seeds(seeds)
    if not argv:
        raise InputError("argv: the experiment command is required after `--`")
    if confirmatory:
        from . import gates

        if not prereg:
            raise InputError("--prereg <PNN> is required with --confirmatory")
        prereg, design_review = gates.confirmatory(lay, prereg)
    else:
        design_review = None
        if prereg:
            from . import prereg as prereg_mod

            prereg_mod._find(lay, prereg)
    cwd = Path(cwd) if cwd else lay.root
    if not cwd.is_dir():
        raise NotFoundError(f"cwd {cwd} does not exist")
    run_id = next_run_id(lay)
    run_dir = lay.runs / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update({"RESEARCH_RUN_DIR": str(run_dir.resolve()), "RESEARCH_SEEDS": json.dumps(seeds), "RESEARCH_RUN_ID": run_id})
    git_sha, dirty = git_state(lay.root, run)
    started = now or now_iso()
    rj = {
        "run_id": run_id,
        "name": name,
        "argv": list(argv),
        "cwd": str(cwd.resolve()),
        "env": {k: env[k] for k in sorted(ENV_ALLOWLIST) if k in env},
        "git_sha": git_sha,
        "dirty_diff_sha256": dirty,
        "expected_seeds": seeds,
        "prereg": prereg,
        "class": "confirmatory" if confirmatory else "exploratory",
        "design_review": design_review,
        "started": started,
        "ended": None,
        "child_exit": None,
        "status": "running",
    }
    _write_run_json(run_dir, rj)
    log_path = run_dir / "output.log"
    with open(log_path, "wb") as log:
        try:
            proc = run(list(argv), cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, start_new_session=True)
            child_exit = proc.returncode
        except subprocess.TimeoutExpired:
            rj.update(status="interrupted", ended=now_iso(), child_exit=None)
            _write_run_json(run_dir, rj)
            write_readme(lay)
            raise SubprocessError(f"{run_id}: timeout after {timeout}s (recorded as interrupted)", data={"run_id": run_id})
        except KeyboardInterrupt:
            rj.update(status="interrupted", ended=now_iso())
            _write_run_json(run_dir, rj)
            write_readme(lay)
            raise
        except OSError as exc:
            rj.update(status="failed", ended=now_iso())
            _write_run_json(run_dir, rj)
            write_readme(lay)
            raise SubprocessError(f"{run_id}: could not start {argv[0]!r}: {exc}", data={"run_id": run_id})
    rj.update(ended=now or now_iso(), child_exit=child_exit)
    if child_exit != 0:
        rj["status"] = "failed"
        _write_run_json(run_dir, rj)
        write_readme(lay)
        raise SubprocessError(f"{run_id}: experiment exited {child_exit} (see {lay.rel(log_path)})", child_exit=child_exit, data={"run_id": run_id})
    findings = _finish_results(run_dir, rj, now=now)
    write_readme(lay)
    if findings:
        raise GateError(f"{run_id}: results.json missing or invalid; run recorded as failed", findings=findings, data={"run_id": run_id})
    return {"status": "completed", "run_id": run_id, "child_exit": 0, "paths": [lay.rel(run_dir)], "class": rj["class"]}


# --- import --------------------------------------------------------------------------


def _check_manifest(src: Path, manifest: dict) -> list[dict]:
    f = []
    rows = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(rows, list) or not rows:
        return [{"severity": "major", "message": "manifest must be {files: [{path, size, sha256}]}", "location": "manifest"}]
    src_res = src.resolve()
    listed = set()
    for i, r in enumerate(rows):
        loc = f"manifest.files[{i}]"
        if not isinstance(r, dict) or not isinstance(r.get("path"), str):
            f.append({"severity": "major", "message": "needs path, size, sha256", "location": loc})
            continue
        rel = r["path"]
        if rel.startswith("/") or ".." in Path(rel).parts or Path(rel).is_absolute():
            f.append({"severity": "major", "message": f"path {rel!r} is absolute or escapes the directory", "location": loc})
            continue
        p = src / rel
        if p.is_symlink():
            f.append({"severity": "major", "message": f"{rel} is a symlink", "location": loc})
            continue
        if not p.is_file():
            f.append({"severity": "major", "message": f"{rel} is missing", "location": loc})
            continue
        try:
            p.resolve().relative_to(src_res)
        except ValueError:
            f.append({"severity": "major", "message": f"{rel} resolves outside the directory", "location": loc})
            continue
        if p.stat().st_size != r.get("size"):
            f.append({"severity": "major", "message": f"{rel}: size {p.stat().st_size} != manifest {r.get('size')}", "location": loc})
        if _sha256(p) != r.get("sha256"):
            f.append({"severity": "major", "message": f"{rel}: sha256 differs from the manifest", "location": loc})
        listed.add(Path(rel).as_posix())
    for p in src.rglob("*"):
        if p.is_symlink():
            f.append({"severity": "major", "message": f"{p.relative_to(src).as_posix()} is a symlink", "location": "directory"})
        elif p.is_file() and p.relative_to(src).as_posix() not in listed:
            f.append({"severity": "major", "message": f"{p.relative_to(src).as_posix()} is present but not in the manifest", "location": "directory"})
    if "results.json" not in listed:
        f.append({"severity": "major", "message": "manifest does not list results.json", "location": "manifest"})
    return f


def import_run(lay: Layout, src: Path, manifest_path: Path, *, name: str, seeds: list[int], prereg: str | None = None, now: str | None = None) -> dict:
    """Copy a run produced elsewhere into ``runs/`` after every file matches the manifest; staged, then renamed."""
    _check_name(name)
    seeds = _check_seeds(seeds)
    src, manifest_path = Path(src), Path(manifest_path)
    if not src.is_dir():
        raise NotFoundError(f"{src} is not a directory")
    if not manifest_path.is_file():
        raise NotFoundError(f"manifest {manifest_path} not found")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InputError(f"manifest is not JSON: {exc}")
    findings = _check_manifest(src, manifest)
    if findings:
        raise GateError(f"import refused: {len(findings)} manifest mismatch(es)", findings=findings)
    try:
        results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GateError("import refused", findings=[{"severity": "major", "message": f"results.json is not JSON: {exc}", "location": "results.json"}])
    findings = validate_results(results)
    if findings:
        raise GateError("import refused: results.json invalid", findings=findings)
    if prereg:
        from . import prereg as prereg_mod

        prereg_mod._find(lay, prereg)
    lay.runs.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=lay.runs))
    try:
        for r in manifest["files"]:
            dst = staging / r["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / r["path"], dst)
        if not (staging / "output.log").exists():
            (staging / "output.log").write_text("", encoding="utf-8")
        run_id = next_run_id(lay)
        git_sha, dirty = git_state(lay.root)
        rj = {
            "run_id": run_id,
            "name": name,
            "argv": None,
            "cwd": None,
            "env": {},
            "git_sha": git_sha,
            "dirty_diff_sha256": dirty,
            "expected_seeds": seeds,
            "prereg": prereg,
            "class": "exploratory",
            "design_review": None,
            "imported_from": {"directory": str(src.resolve()), "manifest_sha256": _sha256(manifest_path), "files": len(manifest["files"])},
            "started": None,
            "ended": now or now_iso(),
            "child_exit": None,
            "status": "completed",
        }
        _write_run_json(staging, rj)
        seal(staging, now=now)
        final = lay.runs / run_id
        os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    write_readme(lay)
    return {"status": "imported", "run_id": run_id, "paths": [lay.rel(final)], "class": "exploratory"}
