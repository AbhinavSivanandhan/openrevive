from uuid import UUID

import pytest

from app.briefing.bedrock_brief_generator import (
    MAX_BRIEF_OUTPUT_TOKENS,
    BriefGenerationError,
    generate_campaign_brief,
)
from app.briefing.evidence_packing import (
    EvidenceDocument,
    build_evidence_bundle,
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
        "Crawler workers use durable leases."
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
    assert str(document_id) in str(messages)
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
