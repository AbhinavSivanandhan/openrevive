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

_SYSTEM_PROMPT = """You produce concise but sufficiently detailed, source-grounded answers.

Use only the supplied evidence cards. Each card has a short reference such as
D01 or D02. Never output document UUIDs.

Answer the distinct parts of the research intent in request order. For a
multi-part request, return one finding per supported part rather than several
findings about the same part. A source that explicitly establishes absence is
evidence: for example, when a contact page contains no public contact details,
state that plainly.

When the user message contains a COVERAGE CHECKLIST, every listed item is
mandatory. Reserve one finding for each checklist item that has evidence.
Combine closely related author facts into one author-background finding instead
of spending multiple findings on that same checklist item. Do not replace a
requested related-material or contact result with a weaker restatement of the
main article.

Do not invent or strengthen claims. An interview, assessment, mention, or
interest is not automatically an offer, employment outcome, endorsement, or
confirmed result. Cite a card only when its evidence text directly supports
the exact statement.

Return JSON only:

{
  "key_findings": [
    {
      "statement": "string",
      "source_refs": ["D01"]
    }
  ],
  "open_questions": [],
  "recommended_follow_ups": []
}

Requirements:
- key_findings contains one to four distinct, useful findings.
- Each finding should be a clear, self-contained explanation rather than a
  one-line summary. Use as much concrete detail as the supplied evidence
  supports, typically one or two substantial sentences.
- Where relevant, include useful source-supported specifics such as APIs,
  methods, mechanisms, examples, caveats, roles, dates, relationships, or
  distinctions between closely related concepts.
- Prefer developing the existing finding over adding another finding that
  repeats the same topic.
- Do not pad with generic background, unsupported examples, or details not
  established by the supplied evidence.
- every finding has one or more supplied source_refs.
- use source_refs exactly as supplied; do not invent or modify them.
- include a negative finding when a requested item is explicitly absent.
- open_questions must be [].
- recommended_follow_ups must be [].
- Always finish a complete valid JSON object. If the response budget is tight,
  shorten or omit lower-priority detail rather than returning partial JSON.
"""

_REDUCER_SYSTEM_PROMPT = """You produce concise but sufficiently detailed,
source-grounded answers.

You receive validated partial findings. Each cited source is represented by a
short reference such as S01 or S02. Never output document UUIDs.

Answer the distinct parts of the research intent in request order. Preserve
one finding per supported part and remove redundant findings. A partial finding
that explicitly establishes absence is evidence and should be retained when it
answers a requested item.

When the user message contains a COVERAGE CHECKLIST, every listed item is
mandatory. Preserve one finding for each checklist item that has evidence.
Combine closely related author facts into one author-background finding instead
of spending multiple findings on that same checklist item. Do not replace a
requested related-material or contact result with a weaker restatement of the
main article.

Do not invent or strengthen claims. Cite a source reference only when the
partial finding directly supports the exact statement.

Return JSON only:

{
  "key_findings": [
    {
      "statement": "string",
      "source_refs": ["S01"]
    }
  ],
  "open_questions": [],
  "recommended_follow_ups": []
}

Requirements:
- key_findings contains one to four distinct, useful findings.
- Each finding should be a clear, self-contained explanation rather than a
  one-line summary. Use as much concrete detail as the validated partial
  findings support, typically one or two substantial sentences.
- Where relevant, retain useful source-supported specifics such as APIs,
  methods, mechanisms, examples, caveats, roles, dates, relationships, or
  distinctions between related concepts.
- Preserve concrete, useful details from the validated partial findings during
  synthesis. Do not reduce supported specifics into broad generic statements
  merely to make the final brief shorter.
- Prefer developing an existing finding over adding another finding that
  repeats the same topic.
- Do not pad with generic background, unsupported examples, or details not
  established by the supplied evidence.
- every finding has one or more supplied source_refs.
- use source_refs exactly as supplied; do not invent or modify them.
- open_questions must be [].
- recommended_follow_ups must be [].
- Always finish a complete valid JSON object. If the response budget is tight,
  shorten or omit lower-priority detail rather than returning partial JSON.
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


def _bundle_source_reference_map(
    evidence_bundle: EvidenceBundle,
) -> dict[str, str]:
    return {
        f"D{index:02d}": str(document_id)
        for index, document_id in enumerate(
            evidence_bundle.included_document_ids,
            start=1,
        )
    }


def _derive_overview(
    findings: list[dict[str, object]],
) -> str:
    statements = [
        str(finding["statement"])
        for finding in findings[:2]
    ]
    return " ".join(statements)


def _parse_model_text(
    *,
    response: dict[str, object],
    source_reference_to_document_id: dict[str, str],
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

    raw_text = "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
    ).strip()

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

    findings = payload.get("key_findings")

    if not isinstance(findings, list) or not findings:
        raise BriefGenerationError(
            "Bedrock response key_findings must be a non-empty list"
        )

    if len(findings) > 4:
        raise BriefGenerationError(
            "Bedrock response key_findings exceeds 4 items"
        )

    allowed_document_ids = set(
        source_reference_to_document_id.values()
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

        raw_source_refs = finding.get("source_refs")
        source_document_ids: list[str] = []

        if isinstance(raw_source_refs, list) and raw_source_refs:
            for raw_source_ref in raw_source_refs:
                source_ref = _non_blank_string(
                    raw_source_ref,
                    field_name=f"key_findings[{index}].source_refs",
                ).upper()

                document_id = source_reference_to_document_id.get(
                    source_ref
                )

                if document_id is None:
                    raise BriefGenerationError(
                        f"Bedrock finding {index} cited an unknown "
                        "source reference"
                    )

                source_document_ids.append(document_id)
        else:
            # Compatibility only for existing deterministic test fixtures.
            # The model prompt instructs production calls to use source_refs.
            raw_source_ids = finding.get("source_document_ids")

            if (
                not isinstance(raw_source_ids, list)
                or not raw_source_ids
            ):
                raise BriefGenerationError(
                    f"Bedrock finding {index} must cite at least one source"
                )

            for raw_source_id in raw_source_ids:
                try:
                    document_id = str(UUID(str(raw_source_id)))
                except (TypeError, ValueError) as exc:
                    raise BriefGenerationError(
                        f"Bedrock finding {index} has invalid source ID"
                    ) from exc

                if document_id not in allowed_document_ids:
                    raise BriefGenerationError(
                        f"Bedrock finding {index} cited an unknown source ID"
                    )

                source_document_ids.append(document_id)

        normalized_findings.append(
            {
                "statement": statement,
                "source_document_ids": list(
                    dict.fromkeys(source_document_ids)
                ),
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
            "overview": _derive_overview(normalized_findings),
            "key_findings": normalized_findings,
            "open_questions": [],
            "recommended_follow_ups": [],
        },
        output_token_count,
    )


async def _invoke_validated_brief(
    *,
    client: BedrockRuntimeClient,
    model_id: str,
    system_prompt: str,
    prompt_text: str,
    source_reference_to_document_id: dict[str, str],
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
        source_reference_to_document_id=(
            source_reference_to_document_id
        ),
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


def _research_intent_from_evidence_bundle(
    evidence_bundle: EvidenceBundle,
) -> str:
    for line in evidence_bundle.evidence_text.splitlines():
        if line.startswith("Research intent:"):
            return line.removeprefix("Research intent:").strip()

    return ""


def _coverage_checklist(research_intent: str) -> str:
    normalized = " ".join(research_intent.lower().split())
    items: list[str] = []

    if "summar" in normalized or "article" in normalized:
        items.append(
            "- Article/content summary: state the core evidence-backed "
            "takeaway from the primary material."
        )

    if any(
        term in normalized
        for term in ("author", "background", "bio", "biography")
    ):
        items.append(
            "- Author background: combine the most relevant role, education, "
            "or experience into one finding when supported."
        )

    if any(
        term in normalized
        for term in ("related", "topic", "topics", "posts", "post")
    ):
        items.append(
            "- Related material or topics: identify distinct related content; "
            "do not merely repeat the primary article."
        )

    if any(
        term in normalized
        for term in (
            "contact",
            "email",
            "phone",
            "linkedin",
            "github",
            "professional",
            "social profile",
        )
    ):
        items.append(
            "- Public professional/contact links: report concrete details "
            "when present, or explicitly state their absence when contact "
            "evidence establishes it."
        )

    if not items:
        return ""

    return (
        "COVERAGE CHECKLIST\n"
        "Every listed item is mandatory when supported by the evidence.\n"
        + "\n".join(items)
    )


def _evidence_prompt(evidence_bundle: EvidenceBundle) -> str:
    checklist = _coverage_checklist(
        _research_intent_from_evidence_bundle(evidence_bundle)
    )

    parts = [
        "Generate the campaign brief from this bounded evidence bundle.",
    ]

    if checklist:
        parts.append(checklist)

    parts.append(evidence_bundle.evidence_text)

    return "\n\n".join(parts)


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
) -> tuple[str, dict[str, str]]:
    checklist = _coverage_checklist(
        plan.research_intent or ""
    )
    parts = [
        "PARTIAL EVIDENCE BRIEFS",
        (
            "Research intent: "
            f"{plan.research_intent or 'Not provided'}"
        ),
    ]

    if checklist:
        parts.append(checklist)

    parts.append("")
    total_characters = len("\n".join(parts))
    source_reference_to_document_id: dict[str, str] = {}
    reference_by_document_id: dict[str, str] = {}

    def source_reference(document_id: str) -> str:
        existing = reference_by_document_id.get(document_id)

        if existing is not None:
            return existing

        reference = (
            f"S{len(reference_by_document_id) + 1:02d}"
        )
        reference_by_document_id[document_id] = reference
        source_reference_to_document_id[reference] = document_id
        return reference

    for index, result in enumerate(group_results, start=1):
        findings = result.brief_json.get("key_findings")

        if not isinstance(findings, list):
            raise BriefGenerationError(
                "validated partial brief did not contain findings"
            )

        reducer_findings: list[dict[str, object]] = []

        for finding in findings:
            if not isinstance(finding, dict):
                raise BriefGenerationError(
                    "validated partial finding was not an object"
                )

            statement = finding.get("statement")
            source_ids = finding.get("source_document_ids")

            if (
                not isinstance(statement, str)
                or not isinstance(source_ids, list)
            ):
                raise BriefGenerationError(
                    "validated partial finding had invalid fields"
                )

            reducer_findings.append(
                {
                    "statement": statement,
                    "source_refs": [
                        source_reference(str(source_id))
                        for source_id in source_ids
                    ],
                }
            )

        encoded_brief = json.dumps(
            {"key_findings": reducer_findings},
            separators=(",", ":"),
            sort_keys=True,
        )
        card = (
            f"[GROUP {index}]\n"
            f"Validated partial findings: {encoded_brief}"
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

    return "\n\n".join(parts), source_reference_to_document_id


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
        source_reference_to_document_id=(
            _bundle_source_reference_map(evidence_bundle)
        ),
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
                source_reference_to_document_id=(
                    _bundle_source_reference_map(group)
                ),
            )
        )

    reducer_prompt, reducer_source_reference_to_document_id = (
        _reducer_prompt(
            plan=evidence_plan,
            group_results=group_results,
        )
    )

    final_result = await _invoke_validated_brief(
        client=client,
        model_id=normalized_model_id,
        system_prompt=_REDUCER_SYSTEM_PROMPT,
        prompt_text=reducer_prompt,
        source_reference_to_document_id=(
            reducer_source_reference_to_document_id
        ),
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
