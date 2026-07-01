from app.crawler.frontier_discovery import (
    discover_links,
    normalize_discovered_url,
)


def test_normalize_discovered_url_keeps_meaningful_query_data() -> None:
    normalized = normalize_discovered_url(
        base_url=(
            "https://docs.example.com/"
            "library/concurrency/overview.html"
        ),
        href=(
            "../asyncio/?utm_source=newsletter"
            "&topic=task-groups#scheduling"
        ),
        allowed_domains=["docs.example.com"],
    )

    assert normalized == (
        "https://docs.example.com/library/asyncio/?topic=task-groups"
    )


def test_normalize_discovered_url_rejects_unsafe_and_external_links() -> None:
    base_url = "https://docs.example.com/library/overview.html"

    for href in (
        "mailto:maintainer@example.com",
        "javascript:void(0)",
        "tel:+1000000000",
        "https://outside.example.net/asyncio.html",
    ):
        assert normalize_discovered_url(
            base_url=base_url,
            href=href,
            allowed_domains=["docs.example.com"],
        ) is None


def test_discover_links_filters_deduplicates_and_ranks_candidates() -> None:
    html = b"""
        <html>
          <body>
            <a href="../asyncio/?utm_source=mail#tasks">
              Asyncio task groups and scheduling
            </a>

            <a href="https://docs.example.com/library/asyncio/?fbclid=abc">
              Duplicate Asyncio page
            </a>

            <a href="/library/task-scheduling.html">
              Task scheduling patterns
            </a>

            <a href="/library/red-soil.html">
              Red soil reference
            </a>

            <a href="/library/next.html">Next</a>

            <a href="https://outside.example.net/unrelated.html">
              External page
            </a>
          </body>
        </html>
    """

    candidates = discover_links(
        base_url=(
            "https://docs.example.com/"
            "library/concurrency/overview.html"
        ),
        html=html,
        allowed_domains=["docs.example.com"],
        research_intent="async task scheduling",
        max_candidates=10,
    )

    assert [candidate.normalized_url for candidate in candidates] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
        "https://docs.example.com/library/red-soil.html",
    ]

    assert [candidate.priority_band for candidate in candidates] == [
        "CORE",
        "RELATED",
        "LOW",
    ]

    assert candidates[0].priority_score > candidates[1].priority_score
    assert candidates[1].priority_score > candidates[2].priority_score

    assert candidates[0].anchor_text == (
        "Asyncio task groups and scheduling"
    )
    assert candidates[0].reason == (
        "anchor and URL match research intent"
    )


def test_discover_links_applies_budget_after_priority_ranking() -> None:
    html = b"""
        <a href="/library/red-soil.html">Red soil reference</a>
        <a href="/library/task-scheduling.html">
          Task scheduling patterns
        </a>
        <a href="/library/asyncio/">Asyncio task groups</a>
    """

    candidates = discover_links(
        base_url="https://docs.example.com/library/overview.html",
        html=html,
        allowed_domains=["docs.example.com"],
        research_intent="async task scheduling",
        max_candidates=2,
    )

    assert [candidate.normalized_url for candidate in candidates] == [
        "https://docs.example.com/library/asyncio/",
        "https://docs.example.com/library/task-scheduling.html",
    ]

def test_discover_links_prioritizes_ipc_and_filters_docs_chrome() -> None:
    html = """
        <html>
          <body>
            <main>
              <a href="/3/library/asyncio-stream.html">
                network IO and IPC
              </a>

              <a href="/3/library/asyncio-eventloop.html">
                networking
              </a>

              <a href="/3/library/socket.html">
                socket - Low-level networking interface
              </a>

              <a href="/3/license.html">
                History and License
              </a>

              <a href="/3/bugs.html">
                Report a bug
              </a>

              <a href="/3/improve-page-nojs.html">
                Improve this page
              </a>
            </main>
          </body>
        </html>
    """.encode()

    candidates = discover_links(
        base_url="https://docs.python.org/3/library/ipc.html",
        html=html,
        allowed_domains=["docs.python.org"],
        research_intent=(
            "Networking and Interprocess Communication, "
            "asynchronous tools"
        ),
        max_candidates=10,
    )

    candidates_by_url = {
        candidate.normalized_url: candidate
        for candidate in candidates
    }

    assert set(candidates_by_url) == {
        "https://docs.python.org/3/library/asyncio-stream.html",
        "https://docs.python.org/3/library/asyncio-eventloop.html",
        "https://docs.python.org/3/library/socket.html",
    }

    assert (
        candidates_by_url[
            "https://docs.python.org/3/library/asyncio-stream.html"
        ].priority_band
        == "CORE"
    )

    assert (
        candidates_by_url[
            "https://docs.python.org/3/library/asyncio-eventloop.html"
        ].priority_band
        == "RELATED"
    )

    assert (
        candidates_by_url[
            "https://docs.python.org/3/library/socket.html"
        ].priority_band
        == "RELATED"
    )

    assert (
        candidates_by_url[
            "https://docs.python.org/3/library/asyncio-stream.html"
        ].priority_score
        > candidates_by_url[
            "https://docs.python.org/3/library/asyncio-eventloop.html"
        ].priority_score
    )


def test_normalize_discovered_url_rejects_non_html_and_embedded_external_paths() -> None:
    base_url = "https://research.example.com/articles/seed"

    for href in (
        "/assets/site.css",
        "/assets/app.js",
        "/assets/logo.svg",
        "/files/resume.pdf",
        "/feed.xml",
        "/robots.txt",
        "/redirects.json",
        "/exports/results.csv",
        "/(https:/outside.example.net/)",
    ):
        assert normalize_discovered_url(
            base_url=base_url,
            href=href,
            allowed_domains=["research.example.com"],
        ) is None


def test_discover_links_keeps_semantically_ambiguous_html_pages_for_ai_selection() -> None:
    html = b"""
        <html>
          <body>
            <a href="/talks/">Talks</a>
            <a href="/publications/">Publications</a>
            <a href="/contact/">Contact</a>
            <a href="/assets/site.css">Stylesheet</a>
            <a href="/files/profile.pdf">Profile PDF</a>
          </body>
        </html>
    """

    candidates = discover_links(
        base_url="https://research.example.com/articles/seed",
        html=html,
        allowed_domains=["research.example.com"],
        research_intent="Any research topic",
        max_candidates=20,
    )

    assert {
        candidate.normalized_url
        for candidate in candidates
    } == {
        "https://research.example.com/talks/",
        "https://research.example.com/publications/",
        "https://research.example.com/contact/",
    }
