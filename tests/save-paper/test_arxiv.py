"""Seam: ``savepaper.arxiv`` public functions.

- ``parse_ref``: pure string -> Ref.
- ``parse_atom``: Atom XML bytes -> list[Meta] (fixtures recorded from export.arxiv.org).
- ``ArxivClient``: HTTP via an injected ``requests``-like session; time via injected clock/sleep.
- ``resolve``: ref + client -> Resolved, or AmbiguousRef / NotFound.
"""

from pathlib import Path

import pytest

from savepaper import arxiv
from savepaper.arxiv import ArxivClient, parse_atom, parse_ref, resolve, safe_id
from savepaper.errors import AmbiguousRef, FetchError, NotFound

FIXTURES = Path(__file__).parent / "fixtures" / "atom"


# --- parse_ref -----------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected_id, expected_version",
    [
        ("https://arxiv.org/abs/2503.17523", "2503.17523", None),
        ("https://arxiv.org/abs/2503.17523v3", "2503.17523", 3),
        ("https://arxiv.org/pdf/2503.17523v2", "2503.17523", 2),
        ("https://arxiv.org/pdf/2503.17523v2.pdf", "2503.17523", 2),
        ("https://arxiv.org/html/2503.17523v3", "2503.17523", 3),
        ("http://export.arxiv.org/abs/2503.17523", "2503.17523", None),
        ("arxiv.org/abs/2503.17523?context=cs", "2503.17523", None),
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001", None),
        ("https://arxiv.org/abs/hep-th/9901001v2", "hep-th/9901001", 2),
        ("arXiv:2503.17523", "2503.17523", None),
        ("2503.17523", "2503.17523", None),
        ("2503.17523v1", "2503.17523", 1),
        ("1706.03762", "1706.03762", None),
        ("hep-th/9901001", "hep-th/9901001", None),
        ("math.GT/0309136", "math.GT/0309136", None),
        ("  2503.17523  ", "2503.17523", None),
    ],
)
def test_parse_ref_ids(ref, expected_id, expected_version):
    parsed = parse_ref(ref)
    assert parsed.kind == "id"
    assert parsed.id == expected_id
    assert parsed.version == expected_version


@pytest.mark.parametrize(
    "ref",
    [
        "Attention Is All You Need",
        "Bayesian Teaching Enables Probabilistic Reasoning",
        "2503",  # too short to be an id
        "https://openreview.net/forum?id=abc",  # not arXiv
    ],
)
def test_parse_ref_titles(ref):
    parsed = parse_ref(ref)
    assert parsed.kind == "title"
    assert parsed.query == ref.strip()


def test_parse_ref_rejects_empty():
    with pytest.raises(ValueError):
        parse_ref("   ")


def test_safe_id_replaces_slash():
    assert safe_id("2503.17523") == "2503.17523"
    assert safe_id("hep-th/9901001") == "hep-th_9901001"


# --- parse_atom ----------------------------------------------------------


def test_parse_atom_single_entry():
    entries = parse_atom((FIXTURES / "2503.17523.xml").read_bytes())
    assert len(entries) == 1
    m = entries[0]
    assert m.id == "2503.17523"
    assert m.version == 3
    assert m.title == "Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models"
    assert m.authors[0] == "Linlu Qiu"
    assert m.authors[-1] == "Sjoerd van Steenkiste"
    assert len(m.authors) == 6
    assert m.published == "2025-03-21T20:13:04Z"
    assert m.updated == "2026-01-15T17:21:57Z"
    assert m.categories == ["cs.CL", "cs.AI"]
    assert m.primary_category == "cs.CL"
    assert m.comment == "Nature Communications"
    assert m.doi == "10.1038/s41467-025-67998-6"
    assert m.summary.startswith("Large language models (LLMs) are increasingly used")
    assert "\n" not in m.summary  # whitespace normalised, content untouched


def test_parse_atom_legacy_id():
    entries = parse_atom((FIXTURES / "hep-th_9901001.xml").read_bytes())
    assert len(entries) == 1
    assert entries[0].id == "hep-th/9901001"
    assert entries[0].version >= 1


def test_parse_atom_empty_feed():
    assert parse_atom((FIXTURES / "notfound.xml").read_bytes()) == []


def test_parse_atom_search_results_keep_order():
    entries = parse_atom((FIXTURES / "search_attention.xml").read_bytes())
    assert len(entries) == 10
    assert entries[0].title == "Attention Is All You Need"
    assert entries[0].id == "1706.03762"


# --- ArxivClient ---------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None, url=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class FakeSession:
    """Records requests; answers from a queue per URL prefix."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        return self.responses.pop(0)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    arxiv.reset_rate_limit()
    yield
    arxiv.reset_rate_limit()


def make_client(responses, clock=None):
    clock = clock or FakeClock()
    session = FakeSession(responses)
    client = ArxivClient(session=session, monotonic=clock.monotonic, sleep=clock.sleep)
    return client, session, clock


def test_client_spaces_requests_three_seconds():
    client, session, clock = make_client([FakeResponse(), FakeResponse(), FakeResponse()])
    client.get("https://export.arxiv.org/api/query?id_list=1")
    clock.now += 1.0  # only one second passed
    client.get("https://export.arxiv.org/api/query?id_list=2")
    client.get("https://export.arxiv.org/api/query?id_list=3")
    assert clock.slept == pytest.approx([2.0, 3.0])
    assert len(session.calls) == 3


def test_rate_limit_is_shared_across_clients():
    clock = FakeClock()
    a, _, _ = make_client([FakeResponse()], clock)
    b, _, _ = make_client([FakeResponse()], clock)
    a.get("https://arxiv.org/html/1")
    b.get("https://arxiv.org/html/2")
    assert clock.slept == pytest.approx([3.0])


def test_client_sends_identifying_user_agent():
    client, session, _ = make_client([FakeResponse()])
    client.get("https://arxiv.org/html/2503.17523v3")
    ua = session.calls[0]["headers"]["User-Agent"]
    assert "save-paper" in ua
    assert "@" in ua or "http" in ua  # contact info


def test_client_honours_retry_after_then_succeeds():
    client, session, clock = make_client(
        [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, b"ok")]
    )
    resp = client.get("https://arxiv.org/html/x")
    assert resp.content == b"ok"
    assert 7.0 in clock.slept
    assert len(session.calls) == 2


def test_client_gives_up_after_three_retries():
    client, session, _ = make_client([FakeResponse(503)] * 4)
    with pytest.raises(FetchError):
        client.get("https://arxiv.org/html/x")
    assert len(session.calls) == 4  # 1 + 3 retries


def test_client_returns_404_without_retrying():
    client, session, _ = make_client([FakeResponse(404)])
    resp = client.get("https://arxiv.org/html/x")
    assert resp.status_code == 404
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/html/x",
        "https://evil.example/arxiv.org/html/x",
        "https://arxiv.org.evil.example/x",
        "file:///etc/passwd",
    ],
)
def test_client_refuses_non_arxiv_urls(url):
    client, session, _ = make_client([])
    with pytest.raises(FetchError):
        client.get(url)
    assert session.calls == []


def test_fetch_urls_are_pinned_to_version():
    client, session, _ = make_client([FakeResponse(), FakeResponse(), FakeResponse()])
    client.fetch_html("2503.17523", 3)
    client.fetch_eprint("2503.17523", 3)
    client.fetch_pdf("hep-th/9901001", 2)
    assert [c["url"] for c in session.calls] == [
        "https://arxiv.org/html/2503.17523v3",
        "https://arxiv.org/e-print/2503.17523v3",
        "https://arxiv.org/pdf/hep-th/9901001v2",
    ]


# --- resolve -------------------------------------------------------------


def atom(name):
    return FakeResponse(200, (FIXTURES / name).read_bytes())


def test_resolve_id_pins_latest_version():
    client, session, _ = make_client([atom("2503.17523.xml")])
    r = resolve("2503.17523", client)
    assert (r.id, r.version) == ("2503.17523", 3)
    assert r.meta.title.startswith("Bayesian Teaching")
    assert "id_list=2503.17523" in session.calls[0]["url"]


def test_resolve_explicit_version_in_ref_wins():
    client, _, _ = make_client([atom("2503.17523.xml")])
    r = resolve("https://arxiv.org/abs/2503.17523v1", client)
    assert r.version == 1


def test_resolve_version_argument_overrides():
    client, _, _ = make_client([atom("2503.17523.xml")])
    r = resolve("2503.17523", client, version=2)
    assert r.version == 2


def test_resolve_unknown_id_raises_not_found():
    client, _, _ = make_client([atom("notfound.xml")])
    with pytest.raises(NotFound):
        resolve("2503.99999", client)


def test_resolve_title_with_many_hits_is_ambiguous_and_lists_candidates():
    client, session, _ = make_client([atom("search_attention.xml")])
    with pytest.raises(AmbiguousRef) as exc:
        resolve("Attention Is All You Need", client)
    cands = exc.value.candidates
    assert len(cands) == 10
    assert cands[0].id == "1706.03762"
    assert cands[0].exact_title is True
    assert cands[1].exact_title is False
    assert 'search_query=ti' in session.calls[0]["url"]


def test_resolve_title_with_single_hit_resolves():
    client, _, _ = make_client([atom("2503.17523.xml")])
    r = resolve("Bayesian Teaching Enables Probabilistic Reasoning in Large Language Models", client)
    assert r.id == "2503.17523"


def test_resolve_title_with_no_hits_raises_not_found():
    client, _, _ = make_client([atom("notfound.xml")])
    with pytest.raises(NotFound):
        resolve("zzzz no such paper qqqq", client)
