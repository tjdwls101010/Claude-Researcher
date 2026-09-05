"""Figure alt-text through an OpenRouter vision model, written into the alt slot only.

Claude reads the Markdown and does not open each PNG, so a figure without text
is a figure that does not exist. The description goes into ``![alt](path)``
because that slot is grammatically separate from caption and body and the
coverage check ignores it -- generated text can never mask a lost block.

Request shape (model, reasoning effort, max_tokens, provider preferences, JSON
schema) is inherited from 성진's measured ``generate_description.js``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .frontmatter import dump, now_iso, parse

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-5.6-luna"
REASONING_EFFORT = "high"
# OpenRouter reserves max_tokens worth of credit up front; the model default (65k) makes a
# nearly-empty balance refuse every request. reasoning=high measured <= 4,660 tokens + ~500 body.
MAX_TOKENS = 16000
PROVIDER_PREFS = {"require_parameters": True, "sort": "price"}
CONTEXT_CHARS = 1111
PROMPT_PATH = Path(__file__).with_name("describe_prompt.md")
ENV_KEY = "OPENROUTER_API_KEY"
ENV_MODEL = "OPENROUTER_MODEL"
ENV_CONCURRENCY = "OPENROUTER_CONCURRENCY"
DEFAULT_CONCURRENCY = 4  # parallel requests per paper; OpenRouter handles this comfortably, the original JS ran all at once
ENV_EFFORT = "OPENROUTER_REASONING_EFFORT"  # none | low | medium | high | xhigh | max
ENV_MAX_TOKENS = "OPENROUTER_MAX_TOKENS"  # raise together with effort: max measured ~12,452 reasoning tokens
LOCAL_ENV = Path(__file__).with_name(".env")  # next to the code, gitignored; .env.example beside it documents the keys

_IMG_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+\.(?:png|jpe?g|gif|webp))\)", re.IGNORECASE)


@dataclass
class DescribeStats:
    model: str
    count: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0})

    def as_frontmatter(self) -> dict:
        return {"by": f"openrouter/{self.model}", "at": now_iso(), "count": self.count, "failed": self.failed}


def env_files(project_root: Optional[Path] = None) -> list[Path]:
    """Where settings are read from, in priority order: the ``.env`` beside this code, then the project root's."""
    files = [LOCAL_ENV]
    if project_root is not None:
        files.append(Path(project_root) / ".env")
    return files


def load_setting(name: str, files: Optional[list[Path]] = None) -> Optional[str]:
    """``name`` from the environment, else the first ``.env`` file (KEY=value lines) that defines it."""
    if os.environ.get(name):
        return os.environ[name]
    for env_file in files if files is not None else env_files():
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def load_api_key(env_file: Optional[Path] = None) -> Optional[str]:
    """``OPENROUTER_API_KEY``; ``env_file`` (a project-root ``.env``) is searched after the code-local one."""
    files = [LOCAL_ENV] + ([env_file] if env_file is not None else [])
    return load_setting(ENV_KEY, files)


def default_model(env_file: Optional[Path] = None) -> str:
    files = [LOCAL_ENV] + ([env_file] if env_file is not None else [])
    return load_setting(ENV_MODEL, files) or DEFAULT_MODEL


def request_settings(env_file: Optional[Path] = None) -> dict:
    """``reasoning.effort`` and ``max_tokens`` for the OpenRouter call, from the environment or ``.env``."""
    files = [LOCAL_ENV] + ([env_file] if env_file is not None else [])
    effort = load_setting(ENV_EFFORT, files) or REASONING_EFFORT
    raw = load_setting(ENV_MAX_TOKENS, files)
    try:
        max_tokens = int(raw) if raw else MAX_TOKENS
    except ValueError:
        max_tokens = MAX_TOKENS
    return {"effort": effort, "max_tokens": max_tokens}


def sanitize_alt(text: str) -> str:
    """One line, no square brackets: real newlines become the two characters ``\\n``, brackets become parentheses."""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n+", "\\\\n", text)
    text = text.replace("[", "(").replace("]", ")")
    text = re.sub(r"^(?:\\n)+|(?:\\n)+$", "", text)
    return text.strip()


def build_request(model: str, prompt: str, image_bytes: bytes, mime: str, effort: str = REASONING_EFFORT, max_tokens: int = MAX_TOKENS) -> dict:
    return {
        "model": model,
        "provider": PROVIDER_PREFS,
        "reasoning": {"effort": effort},
        "max_tokens": max_tokens,
        "usage": {"include": True},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"}},
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "image_description",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "A faithful text replacement for the image, as a single line. Use the two characters \\n where a break is genuinely needed. Never use square brackets.",
                        }
                    },
                    "required": ["description"],
                    "additionalProperties": False,
                },
            },
        },
    }


def parse_response(data: dict) -> str:
    if data.get("error"):
        msg = data["error"].get("message") or json.dumps(data["error"])
        if re.search("credits", msg, re.I):
            msg = f"OpenRouter credits insufficient (max_tokens is reserved up front per request; lower OPENROUTER_MAX_TOKENS in .env). {msg}"
        raise RuntimeError(msg)
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    if not content:
        if choice.get("finish_reason") == "length":
            raise RuntimeError("reasoning used the whole token budget (max_tokens); lower OPENROUTER_REASONING_EFFORT or raise OPENROUTER_MAX_TOKENS in .env")
        raise RuntimeError(f"empty response: {json.dumps(data)[:300]}")
    desc = json.loads(content).get("description", "")
    desc = sanitize_alt(desc)
    if not desc:
        raise RuntimeError("model returned an empty description")
    return desc


def _mime(path: Path) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}[path.suffix.lower().lstrip(".")]


def describe_markdown(
    md_path: Path,
    api_key: str,
    model: Optional[str] = None,
    only_missing: bool = True,
    post: Optional[Callable] = None,
    prompt_template: Optional[str] = None,
    log: Callable[[str], None] = lambda s: None,
    concurrency: Optional[int] = None,
) -> DescribeStats:
    """Fill the alt slot of every raster image link in ``md_path`` (in place) and record the run in frontmatter."""
    if post is None:
        import requests

        def post(payload):
            r = requests.post(ENDPOINT, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload, timeout=300)
            r.raise_for_status()
            return r.json()

    model = model or default_model()
    settings = request_settings()
    template = prompt_template if prompt_template is not None else PROMPT_PATH.read_text(encoding="utf-8")
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse(text)
    stats = DescribeStats(model=model)
    matches = list(_IMG_RE.finditer(body))
    jobs = []  # (match, prompt, image path) for every figure that needs a description
    for m in matches:
        alt, rel = m.group("alt"), m.group("path")
        if only_missing and alt.strip():
            stats.skipped += 1
            continue
        img_path = (md_path.parent / rel).resolve()
        if not img_path.exists():
            stats.failed += 1
            stats.failures.append({"path": rel, "error": "file not found"})
            continue
        before = body[max(0, m.start() - CONTEXT_CHARS) : m.start()].strip()
        after = body[m.end() : m.end() + CONTEXT_CHARS].strip()
        prompt = template.replace("{context_before}", before).replace("{context_after}", after).replace("{image_path}", rel)
        jobs.append((m, prompt, img_path))

    workers = max(1, concurrency or int(load_setting(ENV_CONCURRENCY) or DEFAULT_CONCURRENCY))
    done = [0]
    lock = threading.Lock()
    if jobs:
        log(f"  describing {len(jobs)} figure(s) with {model}, {min(workers, len(jobs))} in parallel")

    def run(job):
        m, prompt, img_path = job
        rel = m.group("path")
        try:
            data = post(build_request(model, prompt, img_path.read_bytes(), _mime(img_path)))
            result = (m, parse_response(data), data.get("usage") or {}, None)
        except Exception as exc:  # network, API, schema -- all leave the alt empty and are counted
            result = (m, None, {}, str(exc)[:300])
        with lock:  # progress as each figure lands, so a caller watching stderr sees the run move
            done[0] += 1
            if result[3] is None:
                log(f"  [{done[0]}/{len(jobs)}] described {rel} ({len(result[1])} chars)")
            else:
                log(f"  [{done[0]}/{len(jobs)}] FAILED {rel}: {result[3][:120]}")
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))

    new_body = body
    for m, desc, usage, error in reversed(results):  # replace from the end so earlier offsets stay valid
        rel = m.group("path")
        if error is not None:
            stats.failed += 1
            stats.failures.append({"path": rel, "error": error})
            continue
        stats.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        stats.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        stats.usage["cost"] = round(stats.usage["cost"] + float(usage.get("cost") or 0.0), 6)
        new_body = new_body[: m.start()] + f"![{desc}]({rel})" + new_body[m.end() :]
        stats.count += 1
    if stats.count or stats.failed:
        log(f"  described {stats.count}/{stats.count + stats.failed}, cost ${stats.usage['cost']}")
    if stats.count or stats.failed:
        fm["figures_described"] = stats.as_frontmatter()
        md_path.write_text(dump(fm, new_body), encoding="utf-8")
    return stats
