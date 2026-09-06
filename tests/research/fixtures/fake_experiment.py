#!/usr/bin/env python3
"""A local stand-in for an experiment: reads RESEARCH_RUN_DIR and RESEARCH_SEEDS, writes results.json.

Values are a deterministic function of condition and seed so a test can predict
the registry. `--fail` exits 3 after writing nothing; `--no-results` exits 0
without a results file; `--tamper` writes results.json then a marker the test
can edit afterwards; `--sleep` waits (for timeout tests); `--bad-schema` writes
an invalid file.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--conditions", default="baseline,method")
p.add_argument("--metric", default="accuracy")
p.add_argument("--fail", action="store_true")
p.add_argument("--no-results", action="store_true")
p.add_argument("--bad-schema", action="store_true")
p.add_argument("--sleep", type=float, default=0)
p.add_argument("--same", action="store_true", help="give every condition identical values (a sanity-check trap)")
p.add_argument("--artifact", action="store_true")
a, _unknown = p.parse_known_args()  # extra args (e.g. redaction tests) are ignored

print("fake experiment starting", flush=True)
print("cwd", os.getcwd(), flush=True)
if a.sleep:
    time.sleep(a.sleep)
if a.fail:
    print("boom", file=sys.stderr)
    sys.exit(3)
run_dir = Path(os.environ["RESEARCH_RUN_DIR"])
seeds = json.loads(os.environ["RESEARCH_SEEDS"])
if a.no_results:
    sys.exit(0)
if a.bad_schema:
    (run_dir / "results.json").write_text('{"schema_version": 1, "observations": "nope"}')
    sys.exit(0)
conds = a.conditions.split(",")
obs = []
for ci, c in enumerate(conds):
    for s in seeds:
        base = 0.70 if a.same else 0.70 + 0.05 * ci
        obs.append({"condition": c, "seed": s, "metrics": {a.metric: f"{base + 0.01 * (s % 3):.4f}"}})
out = {
    "schema_version": 1,
    "metric_def": {a.metric: {"description": "fraction correct", "unit": "ratio", "direction": "maximize"}},
    "conditions": {c: {"config_sha256": hashlib.sha256(f"cfg-{c}".encode()).hexdigest()} for c in conds},
    "observations": obs,
}
(run_dir / "results.json").write_text(json.dumps(out, indent=1))
if a.artifact:
    (run_dir / "artifacts").mkdir(exist_ok=True)
    (run_dir / "artifacts" / "curve.csv").write_text("step,loss\n1,0.5\n")
print("done", flush=True)
