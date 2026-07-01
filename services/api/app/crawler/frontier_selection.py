from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

import boto3

from app.core.config import get_settings
from app.crawler.frontier_discovery import DiscoveredLink


DEFAULT_FRONTIER_SELECTOR_MODEL_ID = (
    "apac.amazon.nova-micro-v1:0"
)
MAX_FRONTIER_SELECTOR_OUTPUT_TOKENS = 320
MAX_CANDIDATE_URL_CHARACTERS = 600
MAX_CANDIDATE_ANCHOR_CHARACTERS = 220

_SYSTEM_PROMPT = """You are a bounded web-research frontier selector.

Given a research intent and candidate URLs already approved by deterministic
crawl policy, select a useful coverage set likely to materially improve a
source-grounded answer. Do not optimize for the smallest possible set.

Remain domain-neutral. Assess only candidate URLs and anchor text; do not
invent page contents. Use the supplied selection target as guidance, while
respecting the hard maximum.

When the research intent explicitly asks for multiple kinds of information,
try to cover distinct requested aspects with different pages when candidates
support them. Examples include the primary article, author or organization
context, tags/topics, related material, and public professional/contact links.

Return JSON only:

{
  "selected_candidate_ids": ["c001", "c002"]
}

Rules:
- Select only supplied candidate IDs.
- Never invent, modify, or repeat IDs.
- Select at most the stated maximum.
- Aim to select up to the stated coverage target when relevant candidates exist.
- Prefer direct and distinct evidence over duplicate, broad, or weak pages.
- Do not select a URL merely because it is on the same site.
"""


class BedrockRuntimeClient(Protocol):
    def converse(self, **kwargs: object) -> dict[str, object]: ...


BedrockClientFactory = Callable[[str], BedrockRuntimeClient]


class FrontierSelectionError(RuntimeError):
    """The bounded frontier-selection request or response was invalid."""


def build_bedrock_runtime_client(
    region_name: str,
) -> BedrockRuntimeClient:
    return boto3.client(
        "bedrock-runtime",
        region_name=region_name,
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _resolve_region_name(region_name: str | None) -> str:
    normalized_region_name = (region_name or "").strip()

    if normalized_region_name:
        return normalized_region_name

    settings = get_settings()

    return (
        (settings.aws_region or "").strip()
        or settings.s3_region_name
    )


def _candidate_cards(
    candidates: list[DiscoveredLink],
) -> tuple[str, dict[str, DiscoveredLink]]:
    cards: list[str] = []
    candidates_by_id: dict[str, DiscoveredLink] = {}

    for index, candidate in enumerate(candidates, start=1):
        candidate_id = f"c{index:03d}"
        candidates_by_id[candidate_id] = candidate

        url = candidate.normalized_url[:MAX_CANDIDATE_URL_CHARACTERS]
        anchor = _normalize_text(candidate.anchor_text)[
            :MAX_CANDIDATE_ANCHOR_CHARACTERS
        ] or "(no anchor text)"

        cards.append(
            f"{candidate_id}\n"
            f"URL: {url}\n"
            f"Anchor: {anchor}"
        )

    return "\n\n".join(cards), candidates_by_id


def _response_text(response: dict[str, object]) -> str:
    output = response.get("output")

    if not isinstance(output, dict):
        raise FrontierSelectionError(
            "Bedrock frontier response did not contain output"
        )

    message = output.get("message")

    if not isinstance(message, dict):
        raise FrontierSelectionError(
            "Bedrock frontier response did not contain a message"
        )

    content = message.get("content")

    if not isinstance(content, list):
        raise FrontierSelectionError(
            "Bedrock frontier response did not contain content"
        )

    text = "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
    ).strip()

    if text.startswith("```json") and text.endswith("```"):
        return text[7:-3].strip()

    if text.startswith("```") and text.endswith("```"):
        return text[3:-3].strip()

    return text


def _selected_candidate_ids(
    *,
    response: dict[str, object],
    candidate_ids: set[str],
    max_selected: int,
) -> list[str]:
    try:
        payload = json.loads(_response_text(response))
    except json.JSONDecodeError as exc:
        raise FrontierSelectionError(
            "Bedrock frontier response was not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise FrontierSelectionError(
            "Bedrock frontier response JSON must be an object"
        )

    selected_ids = payload.get("selected_candidate_ids")

    if not isinstance(selected_ids, list):
        raise FrontierSelectionError(
            "selected_candidate_ids must be a list"
        )

    if len(selected_ids) > max_selected:
        raise FrontierSelectionError(
            "selected_candidate_ids exceeds the requested maximum"
        )

    if not all(isinstance(candidate_id, str) for candidate_id in selected_ids):
        raise FrontierSelectionError(
            "selected_candidate_ids must contain strings"
        )

    if len(set(selected_ids)) != len(selected_ids):
        raise FrontierSelectionError(
            "selected_candidate_ids must not contain duplicates"
        )

    unknown_ids = set(selected_ids).difference(candidate_ids)

    if unknown_ids:
        raise FrontierSelectionError(
            "selected_candidate_ids contained unknown IDs"
        )

    return selected_ids


async def select_research_frontier(
    *,
    candidates: list[DiscoveredLink],
    research_intent: str,
    max_selected: int,
    region_name: str | None = None,
    model_id: str = DEFAULT_FRONTIER_SELECTOR_MODEL_ID,
    client_factory: BedrockClientFactory = (
        build_bedrock_runtime_client
    ),
) -> list[DiscoveredLink]:
    """
    Make one bounded, metadata-only model call for one seed-page frontier.

    The function never accepts model-invented URLs. Any invalid response raises
    FrontierSelectionError so the worker can safely complete the seed without
    mechanically expanding its frontier.
    """
    if max_selected < 0:
        raise ValueError("max_selected must not be negative")

    if not candidates or max_selected == 0:
        return []

    cards, candidates_by_id = _candidate_cards(candidates)
    normalized_intent = _normalize_text(research_intent)

    if not normalized_intent:
        raise ValueError("research_intent must not be blank")

    client = client_factory(_resolve_region_name(region_name))

    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                f"Research intent: {normalized_intent}\n"
                                "Coverage target: "
                                f"{min(8, max_selected)} relevant URLs\n"
                                f"Maximum selected URLs: {max_selected}\n\n"
                                "Candidate pages:\n"
                                f"{cards}"
                            )
                        }
                    ],
                }
            ],
            inferenceConfig={
                "maxTokens": MAX_FRONTIER_SELECTOR_OUTPUT_TOKENS,
                "temperature": 0.0,
            },
        )
    except Exception as exc:
        raise FrontierSelectionError(
            "Bedrock frontier-selection request failed"
        ) from exc

    if not isinstance(response, dict):
        raise FrontierSelectionError(
            "Bedrock frontier-selection response was invalid"
        )

    selected_ids = _selected_candidate_ids(
        response=response,
        candidate_ids=set(candidates_by_id),
        max_selected=max_selected,
    )

    return [
        replace(
            candidates_by_id[candidate_id],
            priority_score=1_000_000 - rank,
            priority_band="SELECTED",
            reason="selected by research-intent frontier",
        )
        for rank, candidate_id in enumerate(selected_ids)
    ]
