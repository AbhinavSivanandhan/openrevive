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


def test_bundle_prioritizes_seed_selected_evidence_and_late_passages() -> None:
    seed = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        source_url="https://example.com/resume-tips",
        content_sha256="a" * 64,
        title="Resume tips for technical job applications",
        extracted_text=(
            "General author background. "
            + ("Unrelated setup material. " * 250)
            + "Resume bullets should quantify scope, action, and outcomes. "
            "Tailor truthful keywords to the target role."
        ),
        depth=0,
    )
    selected_context = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        source_url="https://example.com/contact",
        content_sha256="b" * 64,
        title="Professional contact information",
        extracted_text=(
            "The author provides LinkedIn and contact details for "
            "professional follow-up."
        ),
        depth=1,
        priority_band="SELECTED",
    )
    duplicate_context = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        source_url="https://example.com/contact-copy",
        content_sha256="b" * 64,
        title="Mirrored contact information",
        extracted_text=selected_context.extracted_text,
        depth=1,
        priority_band="SELECTED",
    )

    bundle = build_evidence_bundle(
        documents=[
            duplicate_context,
            selected_context,
            seed,
        ],
        research_intent="resume tips and LinkedIn context",
        model_id="amazon.nova-micro-v1:0",
    )

    assert bundle.included_document_ids[0] == seed.id
    assert selected_context.id in bundle.included_document_ids
    assert duplicate_context.id not in bundle.included_document_ids
    assert "quantify scope, action, and outcomes" in bundle.evidence_text
    assert bundle.input_character_count <= MAX_EVIDENCE_CHARACTERS


def test_bundle_allocates_more_context_to_higher_ranked_evidence() -> None:
    seed = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        source_url="https://example.com/migration-guide",
        content_sha256="c" * 64,
        title="Database migration guide",
        extracted_text=(
            "Migration guidance recommends validating backups before rollout. "
            * 400
        ),
        depth=0,
    )
    supporting = EvidenceDocument(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        source_url="https://example.com/operations",
        content_sha256="d" * 64,
        title="Supporting operational notes",
        extracted_text=("Operational detail. " * 800),
        depth=1,
        priority_band="SELECTED",
    )

    bundle = build_evidence_bundle(
        documents=[supporting, seed],
        research_intent="database migration guide",
        model_id="amazon.nova-micro-v1:0",
    )

    seed_card, supporting_card = bundle.evidence_text.split(
        "[D02]",
        maxsplit=1,
    )

    assert len(seed_card) > len(supporting_card)
