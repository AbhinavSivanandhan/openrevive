from app.api.routers.crawl_runs import normalize_seed_url
from app.crawler.frontier_discovery import discover_links
from app.crawler.worker_main import DEFAULT_MAX_RESPONSE_BYTES


def test_seed_url_uses_discovery_tracking_canonicalization() -> None:
    normalized_url, domain = normalize_seed_url(
        "https://example.com/article?ref=dailydev"
        "&utm_source=newsletter&topic=serverless#summary"
    )

    assert domain == "example.com"
    assert normalized_url == (
        "https://example.com/article?topic=serverless"
    )


def test_discovery_excludes_the_canonical_seed_before_selection() -> None:
    candidates = discover_links(
        base_url="https://example.com/article",
        html=(
            b'<a href="?ref=dailydev">Same article</a>'
            b'<a href="/related">Related evidence</a>'
        ),
        allowed_domains=["example.com"],
        research_intent="related evidence",
        max_candidates=10,
        excluded_urls={"https://example.com/article"},
    )

    assert [candidate.normalized_url for candidate in candidates] == [
        "https://example.com/related"
    ]


def test_default_worker_response_budget_is_four_megabytes() -> None:
    assert DEFAULT_MAX_RESPONSE_BYTES == 4_000_000
