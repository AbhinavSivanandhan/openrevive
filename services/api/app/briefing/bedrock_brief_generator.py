from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import boto3

from app.briefing.evidence_packing import EvidenceBundle


MAX_BRIEF_OUTPUT_TOKENS = 700

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
    normalized_model_id = model_id.strip()
    normalized_region_name = region_name.strip()

    if not normalized_model_id:
        raise ValueError("model_id must not be blank")

    if not normalized_region_name:
        raise ValueError("region_name must not be blank")

    if evidence_bundle.input_document_count <= 0:
        raise ValueError(
            "evidence bundle must contain at least one document"
        )

    client = client_factory(normalized_region_name)

    try:
        response = await asyncio.to_thread(
            client.converse,
            modelId=normalized_model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Generate the campaign brief from this "
                                "bounded evidence bundle:\n\n"
                                f"{evidence_bundle.evidence_text}"
                            )
                        }
                    ],
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
        allowed_document_ids={
            str(document_id)
            for document_id in evidence_bundle.included_document_ids
        },
    )

    return GeneratedCampaignBrief(
        brief_json=brief_json,
        output_token_count=output_token_count,
    )
