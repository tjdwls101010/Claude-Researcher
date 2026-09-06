"""Headless lanes: ``claude -p --agent critic`` and ``codex exec``, judged the same way ``savepaper.note`` judges the note agent.

One agent file (``.claude/agents/critic.md``) owns the prompt: the claude lane
loads it through ``--agent``; the codex lane gets the body pasted in front of
the prompt. Structured output is enforced by a JSON schema on both lanes and
validated here again, because a lane that returns prose is a failed review, not
a review.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from savepaper.describe import load_setting
from savepaper.frontmatter import parse

LANES = ("codex", "claude")
AGENTS_DIR = Path(".claude/agents")
LOCAL_ENV = Path(__file__).with_name(".env")
ENV_CODEX_MODEL = "CODEX_MODEL"
DEFAULT_CODEX_MODEL = "gpt-6-astra"
ENV_CODEX_EFFORT = "CODEX_EFFORT"
# 성진: 30 min per lane call; a draft review of a full paper measured under 10 min, raise when a bigger packet times out
TIMEOUT_S = 30 * 60
_REFUSAL = re.compile(r"can'?t help|cannot assist|cannot help|unable to help|refus|도울 수 없|거부|도와드릴 수 없", re.I)
_AUTH = re.compile(r"not logged in|log in|unauthori[sz]ed|invalid api key|authentication", re.I)


def env_files(project_root: Path) -> list[Path]:
    return [LOCAL_ENV, Path(project_root) / ".env"]


@dataclass
class LaneResult:
    lane: str
    status: str = "failed"  # ok | invalid | refused | failed
    json: Optional[dict] = None
    text: str = ""
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    seconds: Optional[float] = None
    error: Optional[str] = None
    fallback_from: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.json is not None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {})}


# --- schema ---------------------------------------------------------------------------


def validate(obj, schema: dict, path: str = "$") -> list[str]:
    """A small JSON-Schema subset (type, required, properties, items, enum, additionalProperties): enough to refuse a wrong shape with a path."""
    errs = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path}: expected object"]
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k}: required")
        props = schema.get("properties", {})
        for k, v in obj.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: unexpected")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path}: expected array"]
        for i, v in enumerate(obj):
            errs += validate(v, schema.get("items", {}), f"{path}[{i}]")
    elif t == "string":
        if not isinstance(obj, str):
            errs.append(f"{path}: expected string")
    elif t == "boolean":
        if not isinstance(obj, bool):
            errs.append(f"{path}: expected boolean")
    elif t in ("number", "integer"):
        if isinstance(obj, bool) or not isinstance(obj, (int, float)):
            errs.append(f"{path}: expected {t}")
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: must be one of {schema['enum']}")
    return errs


def _extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


# --- commands ---------------------------------------------------------------------------


def agent_body(project_root: Path, agent: str) -> str:
    _, body = parse((Path(project_root) / AGENTS_DIR / f"{agent}.md").read_text(encoding="utf-8"))
    return body


def claude_command(agent: str, schema: dict, tools: tuple[str, ...] = ("Read",)) -> list[str]:
    return ["claude", "-p", "--agent", agent, "--output-format", "json", "--permission-mode", "dontAsk", "--allowedTools", *tools, "--json-schema", json.dumps(schema)]


def codex_command(project_root: Path, schema_path: Path, last_message: Path, model: str, effort: str | None) -> list[str]:
    cmd = ["codex", "exec", "-C", str(project_root), "-s", "read-only", "-m", model, "--ephemeral", "--skip-git-repo-check", "--output-schema", str(schema_path), "-o", str(last_message)]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    return cmd + ["-"]


def _run(cmd, prompt, cwd, run, timeout_s):
    try:
        return run(cmd, input=prompt, capture_output=True, text=True, cwd=str(cwd), timeout=timeout_s, start_new_session=True), None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError as exc:
        return None, f"crash: {exc}"


def _finish(res: LaneResult, payload, text: str, schema: dict) -> LaneResult:
    if payload is None:
        payload = _extract_json(text)
    if payload is None:
        if _REFUSAL.search(text or ""):
            res.status, res.error = "refused", f"refused: {text[:300]}"
        else:
            res.status, res.error = "failed", f"nojson: {(text or '')[:300]}"
        return res
    errs = validate(payload, schema)
    if errs:
        res.status, res.error, res.json = "invalid", "schema: " + "; ".join(errs[:5]), payload
        return res
    res.status, res.json = "ok", payload
    return res


def run_lane(lane: str, prompt: str, *, schema: dict, agent: str, project_root: Path, workdir: Path, run=subprocess.run, timeout_s: float = TIMEOUT_S, tools: tuple[str, ...] = ("Read",)) -> LaneResult:
    """One headless call on one lane; ``workdir`` holds the lane's temp files (schema, last message) and is left clean."""
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    res = LaneResult(lane=lane)
    t0 = time.monotonic()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if lane == "claude":
        proc, err = _run(claude_command(agent, schema, tools), prompt, project_root, run, timeout_s)
        if proc is None:
            res.status, res.error = "failed", err
        elif proc.returncode != 0:
            res.status, res.error = "failed", f"crash: exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}"
        else:
            try:
                data = json.loads(proc.stdout)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                res.status, res.error = "failed", f"crash: non-JSON output: {(proc.stderr or proc.stdout).strip()[-300:]}"
            else:
                text = str(data.get("result") or "")
                res.text = text
                res.model = next(iter((data.get("modelUsage") or {}).keys()), None)
                res.cost_usd = round(float(data["total_cost_usd"]), 4) if data.get("total_cost_usd") is not None else None
                res.turns = data.get("num_turns")
                if data.get("is_error") or _AUTH.search(text[:500]):
                    res.status, res.error = "failed", f"auth: {text[:300]}"
                else:
                    _finish(res, data.get("structured_output"), text, schema)
    else:
        model = load_setting(ENV_CODEX_MODEL, env_files(project_root)) or DEFAULT_CODEX_MODEL
        effort = load_setting(ENV_CODEX_EFFORT, env_files(project_root))
        res.model = model
        schema_path = workdir / f"{agent}.schema.json"
        last = workdir / f"{agent}.last.txt"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        cmd = codex_command(project_root, schema_path, last, model, effort)
        full = agent_body(project_root, agent) + "\n\n---\n\n" + prompt
        proc, err = _run(cmd, full, project_root, run, timeout_s)
        text = last.read_text(encoding="utf-8") if last.exists() else ""
        res.text = text
        if proc is None:
            res.status, res.error = "failed", err
        elif proc.returncode != 0:
            res.status, res.error = "failed", f"crash: exit {proc.returncode}: {proc.stderr.strip()[-300:]}"
        else:
            _finish(res, None, text, schema)
        for p in (schema_path, last):
            if p.exists():
                p.unlink()
    res.seconds = round(time.monotonic() - t0, 1)
    return res


def run_with_fallback(lane: str, prompt: str, **kw) -> LaneResult:
    """The other lane is tried once when the model itself declined or produced no JSON; environment failures are reported as they are."""
    res = run_lane(lane, prompt, **kw)
    if res.status == "refused" or (res.status == "failed" and (res.error or "").startswith("nojson")):
        other = "claude" if lane == "codex" else "codex"
        second = run_lane(other, prompt, **kw)
        second.fallback_from = lane
        second.seconds = round((res.seconds or 0) + (second.seconds or 0), 1)
        return second
    return res
