from __future__ import annotations

import json

import pytest

from app.crawler.frontier_discovery import DiscoveredLink
from app.crawler.frontier_selection import (
    FrontierSelectionError,
    MAX_FRONTIER_SELECTOR_OUTPUT_TOKENS,
    select_research_frontier,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def candidate(
    number: int,
    *,
    url: str,
    anchor: str,
) -> DiscoveredLink:
    return DiscoveredLink(
        normalized_url=url,
        anchor_text=anchor,
        priority_score=number,
        priority_band="LOW",
        reason="candidate",
    )


@pytest.mark.anyio
async def test_selector_accepts_only_returned_candidate_ids_in_model_order() -> None:
    candidates = [
        candidate(
            1,
            url="https://example.com/context",
            anchor="Background context",
        ),
        candidate(
            2,
            url="https://example.com/direct",
            anchor="Direct evidence",
        ),
        candidate(
            3,
            url="https://example.com/extra",
            anchor="Extra detail",
        ),
    ]

    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def converse(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)

            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "selected_candidate_ids": [
                                            "c002",
                                            "c001",
                                        ]
                                    }
                                )
                            }
                        ]
                    }
                }
            }

    client = FakeBedrockClient()

    selected = await select_research_frontier(
        candidates=candidates,
        research_intent="Compare direct evidence and supporting context",
        max_selected=2,
        region_name="ap-south-1",
        client_factory=lambda _: client,
    )

    assert [item.normalized_url for item in selected] == [
        "https://example.com/direct",
        "https://example.com/context",
    ]
    assert [item.priority_band for item in selected] == [
        "SELECTED",
        "SELECTED",
    ]
    assert all(
        item.reason == "selected by research-intent frontier"
        for item in selected
    )

    assert len(client.calls) == 1
    assert client.calls[0]["inferenceConfig"] == {
        "maxTokens": MAX_FRONTIER_SELECTOR_OUTPUT_TOKENS,
        "temperature": 0.0,
    }

    messages = client.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "Maximum selected URLs: 2" in str(messages)
    assert "https://example.com/direct" in str(messages)


@pytest.mark.anyio
async def test_selector_rejects_model_invented_candidate_ids() -> None:
    class FakeBedrockClient:
        def converse(self, **kwargs: object) -> dict[str, object]:
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    '{"selected_candidate_ids":["c999"]}'
                                )
                            }
                        ]
                    }
                }
            }

    with pytest.raises(
        FrontierSelectionError,
        match="unknown IDs",
    ):
        await select_research_frontier(
            candidates=[
                candidate(
                    1,
                    url="https://example.com/page",
                    anchor="Page",
                )
            ],
            research_intent="Any topic",
            max_selected=1,
            region_name="ap-south-1",
            client_factory=lambda _: FakeBedrockClient(),
        )
