from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

_TRACKING_QUERY_PARAMS = {
    "fbclid",
    "gclid",
}

_NAVIGATION_ANCHORS = {
    "next",
    "previous",
    "home",
    "top",
    "up",
    "index",
    "contents",
    "table of contents",
    "navigation",
    "skip to content",
    "read more",
    "more",
}

_CHROME_ANCHORS = {
    "found a bug",
    "history and license",
    "improve this page",
    "report a bug",
    "show source",
}

_CHROME_PATH_SUFFIXES = {
    "/bugs.html",
    "/copyright.html",
    "/genindex.html",
    "/improve-page-nojs.html",
    "/license.html",
    "/py-modindex.html",
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "tool",
    "tools",
    "with",
}

_CONCEPT_ALIASES = {
    "async": "async",
    "asynchronous": "async",
    "asyncio": "async",
    "communication": "ipc",
    "interprocess": "ipc",
    "ipc": "ipc",
    "io": "network",
    "network": "network",
    "networking": "network",
    "socket": "network",
}


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    normalized_url: str
    anchor_text: str
    priority_score: int
    priority_band: str
    reason: str


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return

        href = dict(attrs).get("href")

        if href is None:
            return

        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return

        self.links.append(
            (
                self._href,
                _normalize_whitespace(" ".join(self._text_parts)),
            )
        )
        self._href = None
        self._text_parts = []


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _normalized_allowed_domains(
    allowed_domains: list[str],
) -> set[str]:
    return {
        domain.strip().lower().rstrip(".")
        for domain in allowed_domains
        if domain.strip()
    }


def _domain_is_allowed(
    domain: str,
    allowed_domains: set[str],
) -> bool:
    return any(
        domain == allowed_domain
        or domain.endswith(f".{allowed_domain}")
        for allowed_domain in allowed_domains
    )


def _is_tracking_parameter(key: str) -> bool:
    normalized_key = key.lower()

    return (
        normalized_key.startswith("utm_")
        or normalized_key in _TRACKING_QUERY_PARAMS
    )


def normalize_discovered_url(
    *,
    base_url: str,
    href: str,
    allowed_domains: list[str],
) -> str | None:
    raw_href = href.strip()

    if not raw_href or raw_href.startswith("#"):
        return None

    try:
        resolved_url = urljoin(base_url, raw_href)
        parsed = urlsplit(resolved_url)
        port = parsed.port
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower().rstrip(".")

    if scheme not in {"http", "https"}:
        return None

    if not hostname or parsed.username or parsed.password:
        return None

    if not _domain_is_allowed(
        hostname,
        _normalized_allowed_domains(allowed_domains),
    ):
        return None

    default_port = 80 if scheme == "http" else 443
    hostname_for_netloc = (
        f"[{hostname}]"
        if ":" in hostname
        else hostname
    )

    netloc = (
        f"{hostname_for_netloc}:{port}"
        if port is not None and port != default_port
        else hostname_for_netloc
    )

    filtered_query_pairs = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if not _is_tracking_parameter(key)
    ]

    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            urlencode(filtered_query_pairs, doseq=True),
            "",
        )
    )


def _raw_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _concepts(value: str) -> set[str]:
    concepts: set[str] = set()

    for token in _raw_tokens(value):
        if token in _STOPWORDS:
            continue

        concepts.add(_CONCEPT_ALIASES.get(token, token))

    return concepts


def _path_concepts(normalized_url: str) -> set[str]:
    parsed = urlsplit(normalized_url)
    return _concepts(parsed.path)


def _is_docs_chrome_link(
    *,
    normalized_url: str,
    anchor_text: str,
) -> bool:
    normalized_anchor = _normalize_whitespace(
        anchor_text.lower()
    )
    normalized_path = urlsplit(normalized_url).path.lower()

    if normalized_anchor in _NAVIGATION_ANCHORS:
        return True

    if normalized_anchor in _CHROME_ANCHORS:
        return True

    return any(
        normalized_path.endswith(suffix)
        for suffix in _CHROME_PATH_SUFFIXES
    )


def _rank_link(
    *,
    normalized_url: str,
    anchor_text: str,
    research_intent: str,
) -> DiscoveredLink:
    intent_concepts = _concepts(research_intent)
    anchor_concepts = _concepts(anchor_text)
    url_concepts = _path_concepts(normalized_url)

    anchor_matches = anchor_concepts & intent_concepts
    url_matches = url_concepts & intent_concepts
    all_matches = anchor_matches | url_matches

    ordered_intent_concepts = [
        _CONCEPT_ALIASES.get(token, token)
        for token in _raw_tokens(research_intent)
        if token not in _STOPWORDS
    ]
    primary_intent_concept = (
        ordered_intent_concepts[0]
        if ordered_intent_concepts
        else None
    )

    primary_topic_in_url = (
        primary_intent_concept is not None
        and primary_intent_concept in url_matches
    )

    # CORE means the candidate covers the full stated objective across
    # its anchor text and URL. Partial overlap is deliberately RELATED.
    if (
        intent_concepts
        and all_matches == intent_concepts
        and anchor_matches
        and url_matches
    ):
        priority_band = "CORE"
        reason = "anchor and URL match research intent"
        priority_score = (
            200
            + (len(all_matches) * 25)
            + (len(anchor_matches) * 10)
            + (len(url_matches) * 5)
        )
    elif all_matches:
        priority_band = "RELATED"
        reason = "partial research-intent match"
        priority_score = (
            50
            + (len(all_matches) * 20)
            + (len(anchor_matches) * 10)
            + (len(url_matches) * 5)
        )

        # Prefer a direct URL match for the campaign's first technical
        # topic. For example, /asyncio/ should beat a generic
        # /task-scheduling.html for an "async task scheduling" campaign.
        if primary_topic_in_url:
            priority_score += 25
    else:
        priority_band = "LOW"
        reason = "in scope but weak research-intent match"
        priority_score = 0

    return DiscoveredLink(
        normalized_url=normalized_url,
        anchor_text=anchor_text,
        priority_score=priority_score,
        priority_band=priority_band,
        reason=reason,
    )

def _path_identity(
    normalized_url: str,
) -> tuple[str, str, str]:
    parsed = urlsplit(normalized_url)

    return (
        parsed.scheme,
        parsed.netloc,
        parsed.path,
    )


def _has_query(normalized_url: str) -> bool:
    return bool(urlsplit(normalized_url).query)


def discover_links(
    *,
    base_url: str,
    html: bytes,
    allowed_domains: list[str],
    research_intent: str,
    max_candidates: int,
) -> list[DiscoveredLink]:
    """
    Extract, filter, deduplicate, rank, and budget in-scope links.

    PostgreSQL remains the final campaign-level dedupe authority through:
    UNIQUE (crawl_run_id, normalized_url).
    """
    if max_candidates <= 0:
        return []

    parser = _AnchorExtractor()
    parser.feed(html.decode("utf-8", errors="replace"))
    parser.close()

    candidates_by_path: dict[
        tuple[str, str, str],
        DiscoveredLink,
    ] = {}

    for href, anchor_text in parser.links:
        normalized_url = normalize_discovered_url(
            base_url=base_url,
            href=href,
            allowed_domains=allowed_domains,
        )

        if normalized_url is None:
            continue

        if _is_docs_chrome_link(
            normalized_url=normalized_url,
            anchor_text=anchor_text,
        ):
            continue

        candidate = _rank_link(
            normalized_url=normalized_url,
            anchor_text=anchor_text,
            research_intent=research_intent,
        )

        identity = _path_identity(normalized_url)
        existing = candidates_by_path.get(identity)

        if existing is None:
            candidates_by_path[identity] = candidate
            continue

        existing_has_query = _has_query(existing.normalized_url)
        candidate_has_query = _has_query(candidate.normalized_url)

        if existing_has_query and not candidate_has_query:
            candidates_by_path[identity] = replace(
                existing,
                normalized_url=candidate.normalized_url,
            )
            continue

        if (
            candidate.priority_score > existing.priority_score
            or (
                candidate.priority_score == existing.priority_score
                and candidate.normalized_url < existing.normalized_url
            )
        ):
            if not existing_has_query and candidate_has_query:
                candidate = replace(
                    candidate,
                    normalized_url=existing.normalized_url,
                )

            candidates_by_path[identity] = candidate

    return sorted(
        candidates_by_path.values(),
        key=lambda candidate: (
            -candidate.priority_score,
            candidate.normalized_url,
        ),
    )[:max_candidates]
