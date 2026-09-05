"""Seam: ``describe.describe_markdown(md_path, api_key, post=...)`` with the OpenRouter call injected."""

import json
from pathlib import Path

from savepaper.describe import DEFAULT_MODEL, build_request, describe_markdown, load_api_key, parse_response, sanitize_alt
from savepaper.frontmatter import dump, parse

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
FIX = Path(__file__).parent / "fixtures" / "2503.17523v3"


def ok_response(desc, cost=0.0123):
    return {
        "choices": [{"message": {"content": json.dumps({"description": desc})}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 300, "cost": cost},
    }


def make_md(tmp_path, alts=("", "", "already described")):
    (tmp_path / "images" / "2503.17523v3").mkdir(parents=True)
    body = ["# Paper", "", "Intro paragraph about flights.", ""]
    for i, alt in enumerate(alts, start=1):
        (tmp_path / "images" / "2503.17523v3" / f"fig{i}.png").write_bytes(PNG)
        body += [f"![{alt}](images/2503.17523v3/fig{i}.png)", "", f"*Figure {i}: caption {i}*", ""]
    md = tmp_path / "2503.17523.md"
    md.write_text(dump({"type": "Paper", "title": "T"}, "\n".join(body)))
    return md


def test_describe_fills_empty_alts_and_records_frontmatter(tmp_path):
    md = make_md(tmp_path)
    seen = []

    def post(payload):
        seen.append(payload)
        return ok_response("Bar chart: A [wins] 40 vs 30.\nSecond line")

    stats = describe_markdown(md, "sk-or-test", post=post, prompt_template="CTX:{context_before}|{context_after}|{image_path}")
    assert stats.count == 2 and stats.failed == 0 and stats.skipped == 1
    fm, body = parse(md.read_text())
    assert body.count("![Bar chart: A (wins) 40 vs 30.\\nSecond line](images/2503.17523v3/fig") == 2
    assert "![already described](images/2503.17523v3/fig3.png)" in body
    assert fm["figures_described"]["count"] == 2
    assert fm["figures_described"]["by"] == f"openrouter/{DEFAULT_MODEL}"
    assert stats.usage["cost"] == round(0.0123 * 2, 6)
    # request shape inherited from generate_description.js
    p = seen[0]
    assert p["model"] == DEFAULT_MODEL
    assert p["reasoning"] == {"effort": "high"} and p["max_tokens"] == 16000
    assert p["provider"] == {"require_parameters": True, "sort": "price"}
    assert p["response_format"]["json_schema"]["strict"] is True
    assert p["messages"][0]["content"][0]["type"] == "text"
    assert p["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    # the prompt carries the surrounding paper text, so captions inform the description
    assert "caption" in p["messages"][0]["content"][0]["text"]


def test_failed_image_keeps_empty_alt_and_is_counted(tmp_path):
    md = make_md(tmp_path, alts=("", ""))
    calls = {"n": 0}

    def post(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"error": {"message": "This request requires more credits"}}
        return ok_response("fine")

    stats = describe_markdown(md, "k", post=post, prompt_template="x")
    assert stats.count == 1 and stats.failed == 1
    assert "credits" in stats.failures[0]["error"]
    fm, body = parse(md.read_text())
    assert "![](images/2503.17523v3/" in body and "![fine](images/2503.17523v3/" in body
    assert fm["figures_described"]["failed"] == 1


def test_only_missing_false_redescribes_everything(tmp_path):
    md = make_md(tmp_path, alts=("old",))
    stats = describe_markdown(md, "k", only_missing=False, post=lambda p: ok_response("new"), prompt_template="x")
    assert stats.count == 1
    assert "![new](" in md.read_text()


def test_missing_image_file_is_a_failure_without_calling_the_api(tmp_path):
    md = tmp_path / "p.md"
    md.write_text(dump({"type": "Paper"}, "![](images/none.png)\n"))
    stats = describe_markdown(md, "k", post=lambda p: (_ for _ in ()).throw(AssertionError("no call")), prompt_template="x")
    assert stats.failed == 1 and stats.failures[0]["error"] == "file not found"


def test_parse_response_length_exhaustion_is_explicit():
    import pytest

    with pytest.raises(RuntimeError) as exc:
        parse_response({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    assert "token budget" in str(exc.value)


def test_sanitize_alt():
    assert sanitize_alt("a\r\nb\n\nc") == "a\\nb\\nc"
    assert sanitize_alt("arr[0]") == "arr(0)"
    assert sanitize_alt("\n\nx\n") == "x"


def test_settings_come_from_env_then_code_local_dotenv_then_project_dotenv(tmp_path, monkeypatch):
    from savepaper import describe as d

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    local = tmp_path / "local.env"
    monkeypatch.setattr(d, "LOCAL_ENV", local)
    root = tmp_path / ".env"
    assert load_api_key(root) is None
    assert d.default_model(root) == DEFAULT_MODEL
    root.write_text('# comment\nOTHER=1\nOPENROUTER_API_KEY="sk-or-root"\n')
    assert load_api_key(root) == "sk-or-root"
    local.write_text("OPENROUTER_API_KEY=sk-or-local\nOPENROUTER_MODEL=openai/gpt-5.6\n")
    assert load_api_key(root) == "sk-or-local"  # the .env beside the code wins over the project root
    assert d.default_model(root) == "openai/gpt-5.6"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert load_api_key(root) == "sk-or-env"  # the environment wins over both files
    assert (Path(d.__file__).parent / ".env.example").read_text().startswith("# Copy to .env")
    # reasoning effort and max_tokens follow the same lookup
    assert d.request_settings(root) == {"effort": "high", "max_tokens": 16000}
    local.write_text("OPENROUTER_REASONING_EFFORT=max\nOPENROUTER_MAX_TOKENS=32000\n")
    assert d.request_settings(root) == {"effort": "max", "max_tokens": 32000}
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "low")
    assert d.request_settings(root)["effort"] == "low"
