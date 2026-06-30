from uuid import UUID

from app.briefing.evidence_packing import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_DOCUMENTS,
    EvidenceDocument,
    build_evidence_bundle,
)


def make_document(
    number: int,
    *,
    title: str = "General reference",
    text: str = "General technical material.",
    content_sha256: str | None = None,
) -> EvidenceDocument:
    return EvidenceDocument(
        id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
        source_url=f"https://docs.example.com/page-{number}",
        content_sha256=content_sha256 or f"{number:064x}",
        title=title,
        extracted_text=text,
    )


def test_bundle_is_bounded_and_deterministic() -> None:
    documents = [
        make_document(
            number,
            text=("evidence " * 200),
        )
        for number in range(1, 61)
    ]

    first = build_evidence_bundle(
        documents=documents,
        research_intent="distributed crawler architecture",
        model_id="amazon.nova-micro-v1:0",
    )
    second = build_evidence_bundle(
        documents=list(reversed(documents)),
        research_intent="distributed crawler architecture",
        model_id="amazon.nova-micro-v1:0",
    )

    assert first.corpus_fingerprint == second.corpus_fingerprint
    assert first.evidence_text == second.evidence_text
    assert first.input_document_count <= MAX_EVIDENCE_DOCUMENTS
    assert first.input_character_count <= MAX_EVIDENCE_CHARACTERS
    assert first.available_document_count == 60
    assert len(first.included_document_ids) == first.input_document_count


def test_bundle_prioritizes_research_intent_overlap() -> None:
    generic = make_document(
        1,
        title="Overview",
        text="A broad introduction.",
    )
    relevant = make_document(
        2,
        title="Distributed crawler leases",
        text=(
            "Lease-based distributed crawler workers coordinate "
            "the frontier."
        ),
    )

    bundle = build_evidence_bundle(
        documents=[generic, relevant],
        research_intent="distributed crawler leases",
        model_id="amazon.nova-micro-v1:0",
    )

    assert bundle.included_document_ids[0] == relevant.id
    assert "[D01]" in bundle.evidence_text
    assert "Distributed crawler leases" in bundle.evidence_text


def test_fingerprint_changes_when_evidence_contract_changes() -> None:
    document = make_document(1)

    baseline = build_evidence_bundle(
        documents=[document],
        research_intent="crawler reliability",
        model_id="amazon.nova-micro-v1:0",
    )
    changed_content = build_evidence_bundle(
        documents=[
            make_document(
                1,
                content_sha256="f" * 64,
            )
        ],
        research_intent="crawler reliability",
        model_id="amazon.nova-micro-v1:0",
    )
    changed_intent = build_evidence_bundle(
        documents=[document],
        research_intent="crawler cost controls",
        model_id="amazon.nova-micro-v1:0",
    )
    changed_model = build_evidence_bundle(
        documents=[document],
        research_intent="crawler reliability",
        model_id="amazon.nova-lite-v1:0",
    )

    assert (
        baseline.corpus_fingerprint
        != changed_content.corpus_fingerprint
    )
    assert (
        baseline.corpus_fingerprint
        != changed_intent.corpus_fingerprint
    )
    assert (
        baseline.corpus_fingerprint
        != changed_model.corpus_fingerprint
    )


def test_bundle_ignores_empty_document_text() -> None:
    bundle = build_evidence_bundle(
        documents=[
            make_document(1, text=""),
            make_document(2, text="   "),
            make_document(3, text="Useful persisted evidence."),
        ],
        research_intent=None,
        model_id="amazon.nova-micro-v1:0",
    )

    assert bundle.available_document_count == 1
    assert bundle.input_document_count == 1
    assert bundle.included_document_ids == (
        UUID("00000000-0000-0000-0000-000000000003"),
    )
