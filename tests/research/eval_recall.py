#!/usr/bin/env python3
"""Critic recall baseline: real reviews of a seeded-defect project and its clean control, per lane, repeated.

    python3 tests/research/eval_recall.py --manifest tests/research/fixtures/seeded/manifest.json --lanes codex:2,claude:2 --out /tmp/recall.json

Not collected by pytest (no ``test_`` prefix). Each repetition builds both
projects fresh in a temp directory outside the repo, runs ``review request
--scope design`` on the given lane, and matches findings to the manifest's
defects by location pattern AND mechanism keyword. Prints strict recall per
lane per repetition and the clean control's major findings for a human to
judge; nothing here is a pass mark.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "skills" / "research" / "scripts"))
sys.path.insert(0, str(REPO / ".claude" / "skills" / "save-paper" / "scripts"))
BUILD = REPO / "tests" / "research" / "fixtures" / "seeded" / "build.py"


def match(findings: list[dict], defects: list[dict]) -> tuple[dict, list[dict]]:
    detected: dict[str, str | None] = {d["id"]: None for d in defects}
    unmatched = []
    for f in findings:
        text = " ".join(str(f.get(k, "")) for k in ("location", "observation", "evidence", "why_it_matters", "requested_action")).lower()
        loc = str(f.get("location", "")).lower()
        hit = None
        for d in defects:
            if detected[d["id"]]:
                continue
            if any(p.lower() in loc for p in d["location_patterns"]) and any(k.lower() in text for k in d["keywords"]):
                hit = d["id"]
                break
        if hit:
            detected[hit] = f["id"]
        else:
            unmatched.append({"id": f["id"], "severity": f.get("severity"), "location": f.get("location"), "observation": f.get("observation")})
    return detected, unmatched


def one(lane: str, rep: int, manifest: dict) -> dict:
    from research import project, review

    tmp = Path(tempfile.mkdtemp(prefix="research-eval-"))
    subprocess.run([sys.executable, str(BUILD), "--root", str(tmp)], check=True, capture_output=True)
    out = {"lane": lane, "rep": rep}
    for which in ("defective", "clean"):
        lay = project.Layout.open(tmp / which, manifest["project"])
        t0 = time.monotonic()
        try:
            res = review.request(lay, scope=manifest["scope"], lane=lane)
        except Exception as exc:  # a failed lane is a data point, not a crash of the evaluation
            out[which] = {"error": f"{type(exc).__name__}: {exc}", "seconds": round(time.monotonic() - t0, 1)}
            continue
        from savepaper.frontmatter import parse

        fm, _ = parse((lay.reviews / f"{res['id']}-{res['lane']}.md").read_text(encoding="utf-8"))
        findings = fm["findings"]
        row = {"lane_used": res["lane"], "fallback_from": res.get("fallback_from"), "verdict": fm["verdict"], "n_findings": len(findings), "n_major": sum(1 for f in findings if f["severity"] == "major"), "seconds": round(time.monotonic() - t0, 1), "cost_usd": fm.get("cost_usd"), "review_file": str(lay.reviews / f"{res['id']}-{res['lane']}.md"), "criteria": fm["stage1"]}
        if which == "defective":
            detected, unmatched = match(findings, manifest["defects"])
            row["detected"] = detected
            row["recall"] = sum(1 for v in detected.values() if v) / len(manifest["defects"])
            row["unmatched"] = unmatched
        else:
            row["majors"] = [{"id": f["id"], "location": f["location"], "observation": f["observation"]} for f in findings if f["severity"] == "major"]
        out[which] = row
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="path to the seeded-defect manifest")
    ap.add_argument("--lanes", default="codex:2,claude:2", help="lane:repetitions, comma-separated")
    ap.add_argument("--out", help="write the full results JSON here")
    a = ap.parse_args(argv)
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    results = []
    for part in a.lanes.split(","):
        lane, n = part.split(":")
        for rep in range(1, int(n) + 1):
            print(f"[{lane} #{rep}] running ...", file=sys.stderr, flush=True)
            r = one(lane, rep, manifest)
            results.append(r)
            d, c = r.get("defective", {}), r.get("clean", {})
            print(f"[{lane} #{rep}] defective: recall={d.get('recall')} majors={d.get('n_major')} findings={d.get('n_findings')} {d.get('error', '')} | clean: majors={c.get('n_major')} findings={c.get('n_findings')} {c.get('error', '')}", file=sys.stderr, flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps([{k: (v if k in ("lane", "rep") else {kk: vv for kk, vv in v.items() if kk in ("recall", "n_major", "n_findings", "error", "lane_used", "seconds", "cost_usd")}) for k, v in r.items()} for r in results], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
