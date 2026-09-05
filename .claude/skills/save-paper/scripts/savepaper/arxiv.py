"""arXiv reference parsing, metadata lookup and rate-limited fetching.

Public seam:
- ``parse_ref(text)``            -> Ref          (pure)
- ``parse_atom(xml_bytes)``      -> list[Meta]   (pure)
- ``ArxivClient(session, ...)``  -> ``.get`` / ``.lookup`` / ``.search_title`` / ``.fetch_*``
- ``resolve(ref, client, version=None)`` -> Resolved, or raises NotFound / AmbiguousRef
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

from . import __version__
from .errors import AmbiguousRef, FetchError, NotFound

USER_AGENT = f"save-paper/{__version__} (Claude Researcher; mailto:chunghun1@naver.com)"
MIN_INTERVAL = 3.0  # arXiv API terms: one request every three seconds
MAX_RETRIES = 3
ALLOWED_HOSTS = ("arxiv.org", "export.arxiv.org")

# New-style id: YYMM.NNNNN (4 or 5 digits since 2015). Legacy: archive[.subject]/YYMMNNN.
_NEW_ID = r"(?P<new>\d{4}\.\d{4,5})"
_OLD_ID = r"(?P<old>[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})"
_VERSION = r"(?:v(?P<version>\d+))?"
_ID_RE = re.compile(rf"^(?:arxiv:)?(?:{_NEW_ID}|{_OLD_ID}){_VERSION}$", re.IGNORECASE)
_URL_PATH_RE = re.compile(
    rf"^/(?:abs|pdf|html|e-print|src|format)/(?:{_NEW_ID}|{_OLD_ID}){_VERSION}(?:\.pdf)?/?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Ref:
    kind: str  # "id" | "title"
    id: Optional[str] = None
    version: Optional[int] = None
    query: Optional[str] = None


@dataclass
class Meta:
    id: str
    version: int
    title: str
    summary: str
    authors: list[str] = field(default_factory=list)
    published: str = ""
    updated: str = ""
    categories: list[str] = field(default_factory=list)
    primary_category: str = ""
    comment: str = ""
    doi: str = ""
    journal_ref: str = ""
    exact_title: bool = False  # set by resolve() on title-search candidates

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "authors": self.authors,
            "published": self.published,
            "updated": self.updated,
            "categories": self.categories,
            "primary_category": self.primary_category,
            "comment": self.comment,
            "doi": self.doi,
            "journal_ref": self.journal_ref,
            "exact_title": self.exact_title,
        }


@dataclass
class Resolved:
    id: str
    version: int
    meta: Meta

    @property
    def vid(self) -> str:
        return f"{self.id}v{self.version}"


def _ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def safe_id(arxiv_id: str) -> str:
    """Filesystem-safe form of an arXiv id: legacy ``hep-th/9901001`` -> ``hep-th_9901001``."""
    return arxiv_id.replace("/", "_")


def parse_ref(text: str) -> Ref:
    """Classify user input as an arXiv id (with optional version) or a title query."""
    text = text.strip()
    if not text:
        raise ValueError("empty reference")
    m = _ID_RE.match(text)
    if m:
        return _ref_from_match(m)
    candidate = text if "://" in text else "http://" + text
    parts = urlsplit(candidate)
    host = parts.hostname or ""
    if host in ALLOWED_HOSTS or host.endswith(".arxiv.org"):
        m = _URL_PATH_RE.match(parts.path)
        if m:
            return _ref_from_match(m)
    return Ref(kind="title", query=text)


def _ref_from_match(m: re.Match) -> Ref:
    arxiv_id = m.group("new") or m.group("old")
    version = int(m.group("version")) if m.group("version") else None
    return Ref(kind="id", id=arxiv_id, version=version)


# --- Atom ---------------------------------------------------------------------

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_ENTRY_ID_RE = re.compile(r"/abs/(?P<id>.+?)v(?P<version>\d+)$")


def _attr(el, name: str) -> str:
    return el.get(name, "") if el is not None else ""


def parse_atom(xml_bytes: bytes) -> list[Meta]:
    """Parse an export.arxiv.org Atom feed into Meta entries, in feed order."""
    root = ET.fromstring(xml_bytes)
    out = []
    for entry in root.findall("a:entry", _NS):
        raw_id = _ws(entry.findtext("a:id", default="", namespaces=_NS))
        m = _ENTRY_ID_RE.search(raw_id)
        if not m:
            continue
        out.append(
            Meta(
                id=m.group("id"),
                version=int(m.group("version")),
                title=_ws(entry.findtext("a:title", default="", namespaces=_NS)),
                summary=_ws(entry.findtext("a:summary", default="", namespaces=_NS)),
                authors=[_ws(a.text) for a in entry.findall("a:author/a:name", _NS)],
                published=_ws(entry.findtext("a:published", default="", namespaces=_NS)),
                updated=_ws(entry.findtext("a:updated", default="", namespaces=_NS)),
                categories=[c.get("term", "") for c in entry.findall("a:category", _NS)],
                primary_category=_attr(entry.find("arxiv:primary_category", _NS), "term"),
                comment=_ws(entry.findtext("arxiv:comment", default="", namespaces=_NS)),
                doi=_ws(entry.findtext("arxiv:doi", default="", namespaces=_NS)),
                journal_ref=_ws(entry.findtext("arxiv:journal_ref", default="", namespaces=_NS)),
            )
        )
    return out


# --- HTTP ---------------------------------------------------------------------

# Process-wide so several ArxivClient instances (or a batch loop) share one clock.
_rate_lock = threading.Lock()
_last_request: Optional[float] = None


def reset_rate_limit() -> None:
    global _last_request
    with _rate_lock:
        _last_request = None


class ArxivClient:
    def __init__(
        self,
        session=None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
    ):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.monotonic = monotonic
        self.sleep = sleep
        self.timeout = timeout

    def _wait_turn(self) -> None:
        global _last_request
        with _rate_lock:
            now = self.monotonic()
            if _last_request is not None:
                gap = MIN_INTERVAL - (now - _last_request)
                if gap > 0:
                    self.sleep(gap)
                    now = self.monotonic()
            _last_request = now

    def get(self, url: str):
        """GET with rate limiting, Retry-After, and bounded backoff. Only arxiv.org hosts, only https."""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise FetchError(f"refusing to fetch non-arXiv URL: {url}")
        attempt = 0
        while True:
            self._wait_turn()
            try:
                resp = self.session.get(url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout)
            except Exception as exc:  # requests.RequestException and friends
                resp = None
                err = exc
            else:
                err = None
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp
            attempt += 1
            if attempt > MAX_RETRIES:
                status = resp.status_code if resp is not None else f"error: {err}"
                raise FetchError(f"GET {url} failed after {MAX_RETRIES} retries ({status})")
            retry_after = _retry_after(resp)
            self.sleep(retry_after if retry_after is not None else MIN_INTERVAL * attempt)

    # --- metadata ---

    def lookup(self, arxiv_id: str) -> Optional[Meta]:
        url = f"https://export.arxiv.org/api/query?id_list={quote(arxiv_id, safe='/.-')}&max_results=1"
        resp = self.get(url)
        if resp.status_code != 200:
            raise FetchError(f"arXiv API returned {resp.status_code} for id {arxiv_id}")
        entries = parse_atom(resp.content)
        return entries[0] if entries else None

    def search_title(self, query: str, max_results: int = 10) -> list[Meta]:
        q = quote(f'ti:"{query}"', safe=':"')
        url = f"https://export.arxiv.org/api/query?search_query={q}&max_results={max_results}"
        resp = self.get(url)
        if resp.status_code != 200:
            raise FetchError(f"arXiv API returned {resp.status_code} for title search")
        return parse_atom(resp.content)

    # --- artefacts (all pinned to an exact version) ---

    def fetch_html(self, arxiv_id: str, version: int):
        return self.get(f"https://arxiv.org/html/{arxiv_id}v{version}")

    def fetch_eprint(self, arxiv_id: str, version: int):
        return self.get(f"https://arxiv.org/e-print/{arxiv_id}v{version}")

    def fetch_pdf(self, arxiv_id: str, version: int):
        return self.get(f"https://arxiv.org/pdf/{arxiv_id}v{version}")


def _retry_after(resp) -> Optional[float]:
    if resp is None:
        return None
    value = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


# --- resolve ------------------------------------------------------------------


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def resolve(ref_text: str, client: ArxivClient, version: Optional[int] = None) -> Resolved:
    """Pin a user reference to one exact ``<id>v<n>`` plus its metadata.

    Version precedence: explicit ``version`` argument > version in the ref > latest.
    """
    ref = parse_ref(ref_text)
    if ref.kind == "id":
        meta = client.lookup(ref.id)
        if meta is None:
            raise NotFound(f"arXiv id {ref.id} not found")
    else:
        hits = client.search_title(ref.query)
        if not hits:
            raise NotFound(f"no arXiv paper matches title {ref.query!r}")
        if len(hits) > 1:
            wanted = _norm_title(ref.query)
            for h in hits:
                h.exact_title = _norm_title(h.title) == wanted
            raise AmbiguousRef(ref.query, hits)
        meta = hits[0]
    chosen = version or ref.version or meta.version
    return Resolved(id=meta.id, version=chosen, meta=meta)
