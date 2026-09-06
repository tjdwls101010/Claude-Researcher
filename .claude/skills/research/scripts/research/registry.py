"""The registry: every observed value of every sealed, completed run, with Decimal statistics and sanity warnings.

Values stay strings (Decimal) so rounding happens once, in the paper macro,
where the digit count is explicit. An entry id is ``<run>/<condition>/<metric>``;
names may not contain ``/`` (results validation enforces it).
"""

from __future__ import annotations

import json
from decimal import Context, Decimal, InvalidOperation, localcontext
from pathlib import Path

from . import runs as runs_mod
from .errors import GateError
from .project import Layout, write_readme


def _dec(v: str) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _stats(values: list[Decimal]) -> dict:
    """Sample statistics under an explicit Decimal context: enough precision for the widest input plus guard digits, so the process context cannot change a number."""
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    span = max((len(v.as_tuple().digits) + abs(v.as_tuple().exponent)) for v in values)
    with localcontext(Context(prec=max(28, span + 12))):
        mean = sum(values) / n
        std = (sum((v - mean) ** 2 for v in values) / (n - 1)).sqrt() if n > 1 else None
    return {"n": n, "mean": str(mean.normalize() if mean == mean.to_integral() and False else mean), "std": None if std is None else str(std), "min": str(min(values)), "max": str(max(values))}


def rebuild(lay: Layout, *, min_seeds: int | None = None, strict: bool = False) -> dict:
    entries, excluded, warnings, inputs = [], [], [], {}
    run_dirs = sorted(p for p in lay.runs.glob("r*") if p.is_dir()) if lay.runs.exists() else []
    for rd in run_dirs:
        rid = rd.name
        try:
            rj = json.loads((rd / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            excluded.append({"run_id": rid, "reason": "run.json missing or unreadable"})
            continue
        if rj.get("status") != "completed":
            excluded.append({"run_id": rid, "reason": f"status is {rj.get('status')!r}, not completed (failed/interrupted runs carry no evidence)"})
            continue
        seal, seal_findings = runs_mod.read_seal(rd)
        if seal_findings:
            excluded.append({"run_id": rid, "reason": "seal invalid: " + seal_findings[0]["message"]})
            continue
        # read each sealed input once and index exactly the bytes the seal vouches for
        try:
            run_bytes = (rd / "run.json").read_bytes()
            results_bytes = (rd / "results.json").read_bytes()
        except OSError:
            excluded.append({"run_id": rid, "reason": "run.json or results.json unreadable"})
            continue
        import hashlib

        if hashlib.sha256(run_bytes).hexdigest() != seal["files"]["run.json"] or hashlib.sha256(results_bytes).hexdigest() != seal["files"]["results.json"]:
            excluded.append({"run_id": rid, "reason": "seal does not match: run.json or results.json differ from the sealed bytes"})
            continue
        mismatches = runs_mod.verify_seal(rd)
        if mismatches:
            excluded.append({"run_id": rid, "reason": "seal does not match: " + "; ".join(f"{m['location']} ({m['message']})" for m in mismatches)})
            continue
        try:
            rj = json.loads(run_bytes.decode("utf-8"))
            results = json.loads(results_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            excluded.append({"run_id": rid, "reason": "results.json unreadable"})
            continue
        bad = runs_mod.validate_results(results)
        if bad:
            excluded.append({"run_id": rid, "reason": "results.json invalid: " + bad[0]["message"]})
            continue
        inputs[rid] = {"results_sha256": seal["files"].get("results.json"), "sealed_at": seal.get("sealed_at"), "prereg": rj.get("prereg"), "class": rj.get("class"), "git_sha": rj.get("git_sha"), "design_review": rj.get("design_review")}
        metric_def = results["metric_def"]
        expected = rj.get("expected_seeds") or []
        need = min_seeds if min_seeds is not None else len(expected)
        by_key: dict[tuple[str, str], list] = {}
        for o in results["observations"]:
            for m, v in o["metrics"].items():
                by_key.setdefault((o["condition"], m), []).append((o["seed"], v))
        metrics_seen = sorted({m for _, m in by_key})
        for m in metrics_seen:
            if m not in metric_def:
                warnings.append({"kind": "missing-metric-def", "run_id": rid, "metric": m, "message": f"{rid}: metric {m!r} has no metric_def (unit and direction unknown)"})
        observed_conds = {c for c, _ in by_key}
        for c in sorted(set(results["conditions"]) - observed_conds):
            warnings.append({"kind": "missing-condition", "run_id": rid, "metric": None, "message": f"{rid}: condition {c!r} is declared but has no observations"})
        if expected:
            for c in sorted(observed_conds):
                seeds = sorted({o["seed"] for o in results["observations"] if o["condition"] == c})
                if seeds != sorted(expected):
                    warnings.append({"kind": "unexpected-seeds", "run_id": rid, "metric": None, "message": f"{rid}/{c}: observed seeds {seeds} != expected {sorted(expected)}"})
        for (cond, m), rows in sorted(by_key.items()):
            values, decimals = [], []
            for seed, v in sorted(rows):
                d = _dec(v)
                if d is None:
                    warnings.append({"kind": "non-finite", "run_id": rid, "metric": m, "message": f"{rid}/{cond}/{m}: seed {seed} value {v!r} is not a finite number; dropped from statistics"})
                    continue
                values.append({"seed": seed, "value": str(v)})
                decimals.append(d)
            if len(decimals) < max(need, 1):
                warnings.append({"kind": "too-few-seeds", "run_id": rid, "metric": m, "message": f"{rid}/{cond}/{m}: {len(decimals)} seed(s), {need} expected"})
            md = metric_def.get(m) or {}
            entries.append({
                "id": f"{rid}/{cond}/{m}",
                "run_id": rid,
                "condition": cond,
                "metric": m,
                "values": values,
                "statistics": _stats(decimals),
                "unit": md.get("unit"),
                "direction": md.get("direction"),
                "class": rj.get("class"),
                "prereg": rj.get("prereg"),
            })
        for m in metrics_seen:
            conds = {c: {seed: _dec(v) for seed, v in rows if _dec(v) is not None} for (c, mm), rows in by_key.items() if mm == m}
            if len(conds) < 2:
                continue
            names = sorted(conds)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    if conds[a] and conds[a] == conds[b]:
                        warnings.append({"kind": "identical-values-across-conditions", "run_id": rid, "metric": m, "message": f"{rid}/{m}: conditions {a!r} and {b!r} have identical per-seed values (same code path or a copy bug?)"})
            means = {c: (Decimal(_stats(list(vs.values()))["mean"]) if vs else None) for c, vs in conds.items()}
            if len(set(means.values())) == 1:
                warnings.append({"kind": "identical-means", "run_id": rid, "metric": m, "message": f"{rid}/{m}: every condition has the same mean ({next(iter(means.values()))})"})
    reg = {"schema_version": 1, "entries": entries, "inputs": inputs, "excluded_runs": excluded, "warnings": warnings}
    lay.registry_json.parent.mkdir(parents=True, exist_ok=True)
    lay.registry_json.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    write_readme(lay)
    if strict and warnings:
        raise GateError(f"registry has {len(warnings)} warning(s) (--strict)", findings=[{"severity": "warning", "message": w["message"], "location": w["run_id"]} for w in warnings], data={"entries": len(entries), "excluded_runs": excluded})
    return reg


def load(lay_or_path) -> dict:
    """The registry as written; figure scripts read sealed data through this and nothing else."""
    path = lay_or_path.registry_json if isinstance(lay_or_path, Layout) else Path(lay_or_path)
    if not path.is_file():
        from .errors import NotFoundError

        raise NotFoundError(f"no registry at {path} (run `registry <slug>` after a sealed run)")
    return json.loads(path.read_text(encoding="utf-8"))


def entry(reg: dict, entry_id: str) -> dict | None:
    return next((e for e in reg.get("entries", []) if e.get("id") == entry_id), None)
