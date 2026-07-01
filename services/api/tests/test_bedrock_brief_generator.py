from uuid import UUID

import pytest

from app.briefing.bedrock_brief_generator import (
    MAX_BRIEF_OUTPUT_TOKENS,
    MAX_MAP_REDUCE_MODEL_CALLS,
    BriefGenerationError,
    generate_campaign_brief,
    generate_campaign_brief_from_plan,
)
from app.briefing.evidence_packing import (
    EvidenceDocument,
    build_evidence_bundle,
    build_evidence_plan,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_bundle():
    document = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        source_url="https://docs.example.com/leases",
        content_sha256="a" * 64,
        title="Crawler leases",
        extracted_text=(
            "Database-backed leases prevent duplicate "
            "distributed crawler processing."
        ),
    )

    return build_evidence_bundle(
        documents=[document],
        research_intent="distributed crawler coordination",
        model_id="apac.amazon.nova-micro-v1:0",
    )


@pytest.mark.anyio
async def test_generator_makes_one_bounded_converse_call() -> None:
    bundle = build_bundle()
    document_id = str(bundle.included_document_ids[0])

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
                                "text": (
                                    "```json\n"
                                    "{"
                                    "\"overview\":"
                                    "\"Crawler workers use durable leases.\","
                                    "\"key_findings\":[{"
                                    "\"statement\":"
                                    "\"Leases prevent duplicate work.\","
                                    f"\"source_document_ids\":[\"{document_id}\"]"
                                    "}],"
                                    "\"open_questions\":[],"
                                    "\"recommended_follow_ups\":["
                                    "\"Measure worker lease contention.\""
                                    "]"
                                    "}"
                                    "\n```"
                                )
                            }
                        ]
                    }
                },
                "usage": {
                    "outputTokens": 72,
                },
            }

    client = FakeBedrockClient()

    def client_factory(region_name: str) -> FakeBedrockClient:
        assert region_name == "ap-south-1"
        return client

    result = await generate_campaign_brief(
        evidence_bundle=bundle,
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-south-1",
        client_factory=client_factory,
    )

    assert result.output_token_count == 72
    assert result.brief_json["overview"] == (
        "Leases prevent duplicate work."
    )
    assert result.brief_json["key_findings"] == [
        {
            "statement": "Leases prevent duplicate work.",
            "source_document_ids": [document_id],
        }
    ]

    assert len(client.calls) == 1

    request = client.calls[0]

    assert request["modelId"] == "apac.amazon.nova-micro-v1:0"
    assert request["inferenceConfig"] == {
        "maxTokens": MAX_BRIEF_OUTPUT_TOKENS,
        "temperature": 0.1,
    }

    messages = request["messages"]
    assert isinstance(messages, list)
    assert str(document_id) not in str(messages)
    assert "[D01]" in str(messages)
    assert "raw.html" not in str(messages)


@pytest.mark.anyio
async def test_generator_rejects_unknown_source_document_ids() -> None:
    bundle = build_bundle()

    class FakeBedrockClient:
        def converse(self, **kwargs: object) -> dict[str, object]:
            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": (
                                    "{"
                                    "\"overview\":\"Brief\","
                                    "\"key_findings\":[{"
                                    "\"statement\":\"Unsupported claim\","
                                    "\"source_document_ids\":["
                                    "\"00000000-0000-0000-0000-000000000999\""
                                    "]"
                                    "}],"
                                    "\"open_questions\":[],"
                                    "\"recommended_follow_ups\":[]"
                                    "}"
                                )
                            }
                        ]
                    }
                }
            }

    with pytest.raises(
        BriefGenerationError,
        match="unknown source ID",
    ):
        await generate_campaign_brief(
            evidence_bundle=bundle,
            model_id="apac.amazon.nova-micro-v1:0",
            region_name="ap-south-1",
            client_factory=lambda _: FakeBedrockClient(),
        )


@pytest.mark.anyio
async def test_generator_runs_bounded_map_reduce_for_large_evidence() -> None:
    import json
    import re

    documents = [
        EvidenceDocument(
            id=UUID(
                f"00000000-0000-0000-0000-{number:012d}"
            ),
            source_url=(
                f"https://docs.example.com/source-{number}"
            ),
            content_sha256=f"{number:064x}",
            title=f"Research source {number}",
            extracted_text=(
                f"Source {number} provides evidence for the research "
                "question. " * 800
            ),
            depth=0 if number == 1 else 1,
            priority_band=(
                "LOW" if number == 1 else "SELECTED"
            ),
        )
        for number in range(1, 17)
    ]

    plan = build_evidence_plan(
        documents=documents,
        research_intent="research question",
        model_id="apac.amazon.nova-micro-v1:0",
    )

    assert plan.uses_map_reduce is True

    class FakeBedrockClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def converse(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            request_text = str(kwargs["messages"])
            source_refs = re.findall(
                r"\b[DS]\d{2}\b",
                request_text,
            )
            assert source_refs


            overview = (
                "Reduced answer."
                if "PARTIAL EVIDENCE BRIEFS" in request_text
                else "Partial evidence answer."
            )

            return {
                "output": {
                    "message": {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "overview": overview,
                                        "key_findings": [
                                            {
                                                "statement": (
                                                    "Evidence supports "
                                                    "the research answer."
                                                ),
                                                "source_refs": [source_refs[0]],
                                            }
                                        ],
                                        "open_questions": [],
                                        "recommended_follow_ups": [],
                                    }
                                )
                            }
                        ]
                    }
                },
                "usage": {"outputTokens": 31},
            }

    client = FakeBedrockClient()

    result = await generate_campaign_brief_from_plan(
        evidence_plan=plan,
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-south-1",
        client_factory=lambda _: client,
    )

    expected_call_count = len(plan.map_groups) + 1

    assert len(client.calls) == expected_call_count
    assert expected_call_count <= MAX_MAP_REDUCE_MODEL_CALLS
    assert result.output_token_count == expected_call_count * 31
    assert result.brief_json["overview"] == "Evidence supports the research answer."
    assert result.brief_json["synthesis"] == {
        "mode": "map_reduce",
        "model_call_count": expected_call_count,
    }

    evidence_groups = result.brief_json["evidence_groups"]
    assert isinstance(evidence_groups, list)
    assert len(evidence_groups) == len(plan.map_groups)


@pytest.mark.anyio
async def test_generator_maps_short_refs_and_ignores_model_overview() -> None:
    bundle = build_bundle()
    document_id = str(bundle.included_document_ids[0])

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
                                "text": (
                                    "{"
                                    "\"overview\":\"Invented offer claim\","
                                    "\"key_findings\":[{"
                                    "\"statement\":\"Leases prevent duplicate work.\","
                                    "\"source_refs\":[\"D01\"]"
                                    "}],"
                                    "\"open_questions\":[],"
                                    "\"recommended_follow_ups\":[]"
                                    "}"
                                )
                            }
                        ]
                    }
                }
            }

    client = FakeBedrockClient()

    result = await generate_campaign_brief(
        evidence_bundle=bundle,
        model_id="apac.amazon.nova-micro-v1:0",
        region_name="ap-south-1",
        client_factory=lambda _: client,
    )

    assert result.brief_json["overview"] == (
        "Leases prevent duplicate work."
    )
    assert result.brief_json["key_findings"][0][
        "source_document_ids"
    ] == [document_id]
    assert document_id not in str(client.calls[0]["messages"])
    assert "[D01]" in str(client.calls[0]["messages"])
