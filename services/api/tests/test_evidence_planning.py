from uuid import UUID

from app.briefing.evidence_packing import (
    DIRECT_SYNTHESIS_MAX_EVIDENCE_CHARACTERS,
    MAX_MAP_GROUPS,
    MAX_MAP_GROUP_EVIDENCE_CHARACTERS,
    EvidenceDocument,
    build_evidence_plan,
)


def make_document(
    number: int,
    *,
    text: str,
) -> EvidenceDocument:
    return EvidenceDocument(
        id=UUID(f"00000000-0000-0000-0000-{number:012d}"),
        source_url=f"https://docs.example.com/source-{number}",
        content_sha256=f"{number:064x}",
        title=f"Research source {number}",
        extracted_text=text,
        depth=0 if number == 1 else 1,
        priority_band="SELECTED" if number > 1 else "LOW",
    )


def test_small_corpus_uses_one_direct_evidence_bundle() -> None:
    plan = build_evidence_plan(
        documents=[
            make_document(
                1,
                text="Useful evidence. " * 250,
            ),
            make_document(
                2,
                text="Supporting evidence. " * 200,
            ),
        ],
        research_intent="general research question",
        model_id="apac.amazon.nova-micro-v1:0",
    )

    assert plan.uses_map_reduce is False
    assert plan.direct_bundle is not None
    assert plan.map_groups == ()
    assert (
        plan.input_character_count
        <= DIRECT_SYNTHESIS_MAX_EVIDENCE_CHARACTERS
    )


def test_large_corpus_is_partitioned_into_at_most_four_map_groups() -> None:
    documents = [
        make_document(
            number,
            text=(
                f"Evidence source {number} answers the research question. "
                * 550
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
    assert plan.direct_bundle is None
    assert 2 <= len(plan.map_groups) <= MAX_MAP_GROUPS
    assert all(
        group.input_character_count
        <= MAX_MAP_GROUP_EVIDENCE_CHARACTERS
        for group in plan.map_groups
    )

    included_ids = [
        document_id
        for group in plan.map_groups
        for document_id in group.included_document_ids
    ]

    assert len(included_ids) == len(set(included_ids))


def test_evidence_plan_is_deterministic_regardless_of_input_order() -> None:
    documents = [
        make_document(
            number,
            text=(
                f"Evidence source {number} covers distributed research. "
                * 550
            ),
        )
        for number in range(1, 13)
    ]

    first = build_evidence_plan(
        documents=documents,
        research_intent="distributed research",
        model_id="apac.amazon.nova-micro-v1:0",
    )
    second = build_evidence_plan(
        documents=list(reversed(documents)),
        research_intent="distributed research",
        model_id="apac.amazon.nova-micro-v1:0",
    )

    assert first.corpus_fingerprint == second.corpus_fingerprint
    assert [
        group.evidence_text
        for group in first.map_groups
    ] == [
        group.evidence_text
        for group in second.map_groups
    ]
