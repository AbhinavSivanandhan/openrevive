from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import boto3

from app.briefing.evidence_packing import (
    MAX_MAP_GROUPS,
    EvidenceBundle,
    EvidencePlan,
)


MAX_BRIEF_OUTPUT_TOKENS = 700
MAX_REDUCER_INPUT_CHARACTERS = 24_000
MAX_MAP_REDUCE_MODEL_CALLS = MAX_MAP_GROUPS + 1

_SYSTEM_PROMPT = """You produce concise, evidence-grounded research briefs.

The subject may be any domain. Answer the supplied research intent first.
Use only the supplied campaign evidence. Do not invent facts, URLs, or source
document IDs. State uncertainty as an open question.

Prioritize direct evidence that addresses the research intent. Treat author
biography, site context, or background material as secondary unless it directly
answers the intent. Do not pad the brief with generic observations.

Return JSON only, with this exact shape:

{
  "overview": "string",
  "key_findings": [
    {
      "statement": "string",
      "source_document_ids": ["UUID"]
    }
  ],
  "open_questions": ["string"],
  "recommended_follow_ups": ["string"]
}

Requirements:
- overview must directly answer or frame the research intent concisely.
- key_findings must contain at most five actionable, evidence-backed items.
- every finding must cite at least one supplied document ID.
- open_questions and recommended_follow_ups must each contain at most five
  strings.
"""

_REDUCER_SYSTEM_PROMPT = """You produce concise, evidence-grounded research briefs.

The subject may be any domain. You receive validated partial briefs generated
from separate evidence groups. Answer the supplied research intent first.

Use only the supplied partial briefs and their cited source document IDs. Do
not invent facts, URLs, or source IDs. Preserve uncertainty as an open
question. Prefer direct evidence over generic background context. Do not pad
the answer with generic advice.

Return JSON only, with this exact shape:

{
  "overview": "string",
  "key_findings": [
    {
      "statement": "string",
      "source_document_ids": ["UUID"]
    }
  ],
  "open_questions": ["string"],
  "recommended_follow_ups": ["string"]
}

Requirements:
- overview must directly answer or frame the research intent concisely.
- key_findings must contain at most five actionable, evidence-backed items.
- every finding must cite at least one source ID visible in the partial briefs.
- open_questions and recommended_follow_ups must each contain at most five
  strings.
"""


class BedrockRuntimeClient(Protocol):
    def converse(self, **kwargs: object) -> dict[str, object]: ...


BedrockClientFactory = Callable[[str], BedrockRuntimeClient]


class BriefGenerationError(RuntimeError):
    """The bounded Bedrock brief request or its response was invalid."""


@dataclass(frozen=True, slots=True)
class GeneratedCampaignBrief:
    brief_json: dict[str, object]
    output_token_count: int | None


def build_bedrock_runtime_client(
    region_name: str,
) -> BedrockRuntimeClient:
    return boto3.client(
        "bedrock-runtime",
        region_name=region_name,
    )


def _non_blank_string(
    value: object,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BriefGenerationError(
            f"Bedrock response field {field_name!r} must be non-blank"
        )

    return value.strip()


def _string_list(
    value: object,
    *,
    field_name: str,
    maximum_items: int,
) -> list[str]:
    if not isinstance(value, list):
        raise BriefGenerationError(
            f"Bedrock response field {field_name!r} must be a list"
        )

    if len(value) > maximum_items:
        raise BriefGenerationError(
            f"Bedrock response field {field_name!r} exceeds "
            f"{maximum_items} items"
        )

    return [
        _non_blank_string(
            item,
            field_name=field_name,
        )
        for item in value
    ]


def _parse_model_text(
    *,
    response: dict[str, object],
    allowed_document_ids: set[str],
) -> tuple[dict[str, object], int | None]:
    output = response.get("output")

    if not isinstance(output, dict):
        raise BriefGenerationError(
            "Bedrock response did not contain output"
        )

    message = output.get("message")

    if not isinstance(message, dict):
        raise BriefGenerationError(
            "Bedrock response did not contain output message"
        )

    content = message.get("content")

    if not isinstance(content, list):
        raise BriefGenerationError(
            "Bedrock response did not contain output content"
        )

    text_parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
    ]

    raw_text = "".join(text_parts).strip()

    if raw_text.startswith("```json") and raw_text.endswith("```"):
        raw_text = raw_text[7:-3].strip()
    elif raw_text.startswith("```") and raw_text.endswith("```"):
        raw_text = raw_text[3:-3].strip()

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise BriefGenerationError(
            "Bedrock response was not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise BriefGenerationError(
            "Bedrock response JSON must be an object"
        )

    overview = _non_blank_string(
        payload.get("overview"),
        field_name="overview",
    )

    findings = payload.get("key_findings")

    if not isinstance(findings, list) or not findings:
        raise BriefGenerationError(
            "Bedrock response key_findings must be a non-empty list"
        )

    if len(findings) > 5:
        raise BriefGenerationError(
            "Bedrock response key_findings exceeds 5 items"
        )

    normalized_findings: list[dict[str, object]] = []

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise BriefGenerationError(
                f"Bedrock finding {index} must be an object"
            )

        statement = _non_blank_string(
            finding.get("statement"),
            field_name=f"key_findings[{index}].statement",
        )

        raw_source_ids = finding.get("source_document_ids")

        if (
            not isinstance(raw_source_ids, list)
            or not raw_source_ids
        ):
            raise BriefGenerationError(
                f"Bedrock finding {index} must cite at least one source"
            )

        source_document_ids: list[str] = []

        for raw_source_id in raw_source_ids:
            try:
                normalized_source_id = str(UUID(str(raw_source_id)))
            except (TypeError, ValueError) as exc:
                raise BriefGenerationError(
                    f"Bedrock finding {index} has invalid source ID"
                ) from exc

            if normalized_source_id not in allowed_document_ids:
                raise BriefGenerationError(
                    f"Bedrock finding {index} cited an unknown source ID"
                )

            source_document_ids.append(normalized_source_id)

        normalized_findings.append(
            {
                "statement": statement,
                "source_document_ids": source_document_ids,
            }
        )

    usage = response.get("usage")
    output_token_count: int | None = None

    if isinstance(usage, dict):
        candidate_count = usage.get("outputTokens")

        if isinstance(candidate_count, int):
            output_token_count = candidate_count

    return (
        {
            "overview": overview,
            "key_findings": normalized_findings,
            "open_questions": _string_list(
                payload.get("open_questions"),
                field_name="open_questions",
                maximum_items=5,
            ),
            "recommended_follow_ups": _string_list(
                payload.get("recommended_follow_ups"),
                field_name="recommended_follow_ups",
                maximum_items=5,
            ),
        },
        output_token_count,
    )


async def _invoke_validated_brief(
    *,
    client: BedrockRuntimeClient,
    model_id: str,
    system_prompt: str,
    prompt_text: str,
    allowed_document_ids: set[str],
) -> GeneratedCampaignBrief:
    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt_text}],
                }
            ],
            inferenceConfig={
                "maxTokens": MAX_BRIEF_OUTPUT_TOKENS,
                "temperature": 0.1,
            },
        )
    except Exception as exc:
        raise BriefGenerationError(
            "Bedrock campaign-brief request failed"
        ) from exc

    if not isinstance(response, dict):
        raise BriefGenerationError(
            "Bedrock campaign-brief response was invalid"
        )

    brief_json, output_token_count = _parse_model_text(
        response=response,
        allowed_document_ids=allowed_document_ids,
    )

    return GeneratedCampaignBrief(
        brief_json=brief_json,
        output_token_count=output_token_count,
    )


def _validated_model_and_region(
    *,
    model_id: str,
    region_name: str,
) -> tuple[str, str]:
    normalized_model_id = model_id.strip()
    normalized_region_name = region_name.strip()

    if not normalized_model_id:
        raise ValueError("model_id must not be blank")

    if not normalized_region_name:
        raise ValueError("region_name must not be blank")

    return normalized_model_id, normalized_region_name


def _evidence_prompt(evidence_bundle: EvidenceBundle) -> str:
    return (
        "Generate the campaign brief from this bounded evidence bundle:\n\n"
        f"{evidence_bundle.evidence_text}"
    )


def _source_ids_from_brief(brief_json: dict[str, object]) -> set[str]:
    findings = brief_json.get("key_findings")

    if not isinstance(findings, list):
        raise BriefGenerationError(
            "validated brief did not contain key findings"
        )

    source_ids: set[str] = set()

    for finding in findings:
        if not isinstance(finding, dict):
            raise BriefGenerationError(
                "validated brief finding was not an object"
            )

        raw_source_ids = finding.get("source_document_ids")

        if not isinstance(raw_source_ids, list):
            raise BriefGenerationError(
                "validated brief finding did not contain source IDs"
            )

        for source_id in raw_source_ids:
            if not isinstance(source_id, str):
                raise BriefGenerationError(
                    "validated brief contained a non-string source ID"
                )

            source_ids.add(source_id)

    if not source_ids:
        raise BriefGenerationError(
            "validated brief did not preserve any source IDs"
        )

    return source_ids


def _reducer_prompt(
    *,
    plan: EvidencePlan,
    group_results: list[GeneratedCampaignBrief],
) -> str:
    parts = [
        "PARTIAL EVIDENCE BRIEFS",
        (
            "Research intent: "
            f"{plan.research_intent or 'Not provided'}"
        ),
        "",
    ]
    total_characters = len("\n".join(parts))

    for index, result in enumerate(group_results, start=1):
        encoded_brief = json.dumps(
            result.brief_json,
            separators=(",", ":"),
            sort_keys=True,
        )
        card = (
            f"[GROUP {index}]\n"
            f"Validated partial brief: {encoded_brief}"
        )
        separator = "\n\n"
        projected_characters = (
            total_characters + len(separator) + len(card)
        )

        if projected_characters > MAX_REDUCER_INPUT_CHARACTERS:
            raise BriefGenerationError(
                "partial briefs exceeded the reducer input budget"
            )

        parts.append(card)
        total_characters = projected_characters

    return "\n\n".join(parts)


def _total_output_tokens(
    results: list[GeneratedCampaignBrief],
) -> int | None:
    counts = [
        result.output_token_count
        for result in results
    ]

    if any(count is None for count in counts):
        return None

    return sum(count for count in counts if count is not None)


async def generate_campaign_brief(
    *,
    evidence_bundle: EvidenceBundle,
    model_id: str,
    region_name: str,
    client_factory: BedrockClientFactory = (
        build_bedrock_runtime_client
    ),
) -> GeneratedCampaignBrief:
    """
    Make exactly one bounded Bedrock Converse request.

    Cache reservation happens before this function. The caller must invoke
    this only for a newly-created GENERATING brief row.
    """
    normalized_model_id, normalized_region_name = (
        _validated_model_and_region(
            model_id=model_id,
            region_name=region_name,
        )
    )

    if evidence_bundle.input_document_count <= 0:
        raise ValueError(
            "evidence bundle must contain at least one document"
        )

    client = client_factory(normalized_region_name)

    return await _invoke_validated_brief(
        client=client,
        model_id=normalized_model_id,
        system_prompt=_SYSTEM_PROMPT,
        prompt_text=_evidence_prompt(evidence_bundle),
        allowed_document_ids={
            str(document_id)
            for document_id in evidence_bundle.included_document_ids
        },
    )


async def generate_campaign_brief_from_plan(
    *,
    evidence_plan: EvidencePlan,
    model_id: str,
    region_name: str,
    client_factory: BedrockClientFactory = (
        build_bedrock_runtime_client
    ),
) -> GeneratedCampaignBrief:
    """
    Generate one direct brief or a bounded map-reduce brief.

    The plan is deterministic and is already covered by the campaign cache
    fingerprint. The map-reduce path can make at most four map calls and one
    reducer call.
    """
    normalized_model_id, normalized_region_name = (
        _validated_model_and_region(
            model_id=model_id,
            region_name=region_name,
        )
    )

    if evidence_plan.direct_bundle is not None:
        direct_result = await generate_campaign_brief(
            evidence_bundle=evidence_plan.direct_bundle,
            model_id=normalized_model_id,
            region_name=normalized_region_name,
            client_factory=client_factory,
        )
        direct_json = dict(direct_result.brief_json)
        direct_json["evidence_groups"] = []
        direct_json["synthesis"] = {
            "mode": "direct",
            "model_call_count": 1,
        }

        return GeneratedCampaignBrief(
            brief_json=direct_json,
            output_token_count=direct_result.output_token_count,
        )

    if not evidence_plan.map_groups:
        raise ValueError(
            "evidence plan must contain a direct bundle or map groups"
        )

    if len(evidence_plan.map_groups) > MAX_MAP_GROUPS:
        raise ValueError(
            "evidence plan exceeds the map-group limit"
        )

    client = client_factory(normalized_region_name)
    group_results: list[GeneratedCampaignBrief] = []

    for group in evidence_plan.map_groups:
        if group.input_document_count <= 0:
            raise ValueError(
                "map evidence group must contain at least one document"
            )

        group_results.append(
            await _invoke_validated_brief(
                client=client,
                model_id=normalized_model_id,
                system_prompt=_SYSTEM_PROMPT,
                prompt_text=(
                    "Generate a partial research brief from this evidence "
                    "group. Preserve concrete source-grounded claims for "
                    "later synthesis.\n\n"
                    f"{group.evidence_text}"
                ),
                allowed_document_ids={
                    str(document_id)
                    for document_id in group.included_document_ids
                },
            )
        )

    reducer_allowed_ids = set().union(
        *[
            _source_ids_from_brief(result.brief_json)
            for result in group_results
        ]
    )

    final_result = await _invoke_validated_brief(
        client=client,
        model_id=normalized_model_id,
        system_prompt=_REDUCER_SYSTEM_PROMPT,
        prompt_text=_reducer_prompt(
            plan=evidence_plan,
            group_results=group_results,
        ),
        allowed_document_ids=reducer_allowed_ids,
    )

    final_json = dict(final_result.brief_json)
    final_json["evidence_groups"] = [
        {
            "group_index": index,
            "input_document_count": (
                group.input_document_count
            ),
            "input_character_count": (
                group.input_character_count
            ),
            "source_document_ids": [
                str(document_id)
                for document_id in group.included_document_ids
            ],
            "brief": result.brief_json,
        }
        for index, (group, result) in enumerate(
            zip(
                evidence_plan.map_groups,
                group_results,
                strict=True,
            ),
            start=1,
        )
    ]
    final_json["synthesis"] = {
        "mode": "map_reduce",
        "model_call_count": len(group_results) + 1,
    }

    all_results = [*group_results, final_result]

    if len(all_results) > MAX_MAP_REDUCE_MODEL_CALLS:
        raise RuntimeError(
            "map-reduce generation exceeded the model-call limit"
        )

    return GeneratedCampaignBrief(
        brief_json=final_json,
        output_token_count=_total_output_tokens(all_results),
    )
