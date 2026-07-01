from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID


PROMPT_VERSION = "campaign-brief-v7"
MAX_EVIDENCE_DOCUMENTS = 50
MAX_EVIDENCE_CHARACTERS = 24_000
MIN_CHARACTERS_PER_DOCUMENT = 160
MAX_CHARACTERS_PER_DOCUMENT = 3_600

# The direct path remains cheap for small corpora. Larger corpora are split
# into at most four map bundles, leaving one final reducer call for a hard
# maximum of five Bedrock calls in the next implementation step.
DIRECT_SYNTHESIS_MAX_EVIDENCE_CHARACTERS = 18_000
MAX_MAP_GROUPS = 4
MAX_MAP_GROUP_EVIDENCE_CHARACTERS = 12_000
MAX_TOTAL_MAP_REDUCE_EVIDENCE_CHARACTERS = (
    MAX_MAP_GROUPS * MAX_MAP_GROUP_EVIDENCE_CHARACTERS
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]{2,}")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    id: UUID
    source_url: str
    content_sha256: str
    title: str | None
    extracted_text: str | None
    depth: int = 1
    priority_score: int = 0
    priority_band: str = "LOW"


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    corpus_fingerprint: str
    evidence_text: str
    included_document_ids: tuple[UUID, ...]
    available_document_count: int
    input_document_count: int
    input_character_count: int


@dataclass(frozen=True, slots=True)
class EvidencePlan:
    """
    Deterministic synthesis input plan.

    direct_bundle is populated for the one-call path. map_groups is populated
    for the future map-reduce path. Only one form is active at a time.
    """

    corpus_fingerprint: str
    research_intent: str
    direct_bundle: EvidenceBundle | None
    map_groups: tuple[EvidenceBundle, ...]
    available_document_count: int
    input_document_count: int
    input_character_count: int

    @property
    def uses_map_reduce(self) -> bool:
        return bool(self.map_groups)


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _tokens(value: str | None) -> set[str]:
    return set(_TOKEN_PATTERN.findall((value or "").lower()))


def _score_document(
    document: EvidenceDocument,
    *,
    intent_tokens: set[str],
) -> int:
    """
    Generic evidence ranking. It uses crawl provenance and textual relevance,
    not any topic-specific URL or site rules.
    """
    normalized_text = _normalize_text(document.extracted_text)

    title_matches = len(
        intent_tokens.intersection(_tokens(document.title))
    )
    url_matches = len(
        intent_tokens.intersection(_tokens(document.source_url))
    )
    body_matches = len(
        intent_tokens.intersection(_tokens(normalized_text))
    )

    seed_bonus = 120 if document.depth == 0 else 0
    selected_bonus = (
        65
        if document.priority_band.strip().upper() == "SELECTED"
        else 0
    )
    priority_bonus = min(
        max(document.priority_score, 0),
        1_000,
    ) // 40
    substance_bonus = min(len(normalized_text) // 1_500, 12)

    return (
        seed_bonus
        + selected_bonus
        + priority_bonus
        + title_matches * 20
        + url_matches * 9
        + body_matches * 5
        + substance_bonus
    )


def _rank_documents(
    *,
    documents: list[EvidenceDocument],
    intent_tokens: set[str],
) -> list[tuple[EvidenceDocument, int]]:
    ranked = sorted(
        (
            (
                document,
                _score_document(
                    document,
                    intent_tokens=intent_tokens,
                ),
            )
            for document in documents
        ),
        key=lambda item: (
            -item[1],
            item[0].source_url,
            str(item[0].id),
        ),
    )

    # Persisted duplicate text should not consume several prompt allocations.
    unique_ranked: list[tuple[EvidenceDocument, int]] = []
    seen_content_hashes: set[str] = set()

    for document, score in ranked:
        content_identity = document.content_sha256.strip().lower()

        if content_identity and content_identity in seen_content_hashes:
            continue

        if content_identity:
            seen_content_hashes.add(content_identity)

        unique_ranked.append((document, score))

    return unique_ranked


def _excerpt(
    *,
    value: str | None,
    intent_tokens: set[str],
    max_characters: int,
) -> str:
    normalized = _normalize_text(value)

    if not normalized or max_characters <= 0:
        return ""

    if len(normalized) <= max_characters:
        return normalized

    if not intent_tokens:
        return normalized[:max_characters]

    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_BOUNDARY_PATTERN.split(normalized)
        if sentence.strip()
    ]

    ranked_sentences = [
        (
            len(intent_tokens.intersection(_tokens(sentence))),
            index,
            sentence,
        )
        for index, sentence in enumerate(sentences)
    ]
    relevant_sentences = [
        item
        for item in ranked_sentences
        if item[0] > 0
    ]

    if not relevant_sentences:
        return normalized[:max_characters]

    selected: list[tuple[int, str]] = []
    used_characters = 0

    for _, index, sentence in sorted(
        relevant_sentences,
        key=lambda item: (-item[0], item[1]),
    ):
        separator_characters = 1 if selected else 0
        remaining = (
            max_characters
            - used_characters
            - separator_characters
        )

        if remaining <= 0:
            break

        selected.append((index, sentence[:remaining]))
        used_characters += (
            separator_characters
            + min(len(sentence), remaining)
        )

    return " ".join(
        sentence
        for _, sentence in sorted(selected, key=lambda item: item[0])
    )


def _character_limits(
    ranked_documents: list[tuple[EvidenceDocument, int]],
    *,
    max_evidence_characters: int,
) -> list[int]:
    if not ranked_documents:
        return []

    estimated_metadata_characters = (
        600 + len(ranked_documents) * 220
    )
    text_budget = max(
        0,
        max_evidence_characters - estimated_metadata_characters,
    )
    baseline = min(
        MIN_CHARACTERS_PER_DOCUMENT,
        text_budget // len(ranked_documents),
    )
    distributable = max(
        0,
        text_budget - baseline * len(ranked_documents),
    )
    weights = [max(score, 1) for _, score in ranked_documents]
    total_weight = sum(weights)

    return [
        min(
            MAX_CHARACTERS_PER_DOCUMENT,
            baseline
            + int(distributable * weight / total_weight),
        )
        for weight in weights
    ]


def _fingerprint(
    *,
    documents: list[EvidenceDocument],
    research_intent: str | None,
    model_id: str,
    prompt_version: str,
) -> str:
    payload = {
        "documents": [
            {
                "content_sha256": document.content_sha256,
                "depth": document.depth,
                "id": str(document.id),
                "priority_band": document.priority_band,
                "priority_score": document.priority_score,
                "source_url": document.source_url,
            }
            for document in sorted(
                documents,
                key=lambda item: (item.source_url, str(item.id)),
            )
        ],
        "model_id": model_id,
        "prompt_version": prompt_version,
        "research_intent": _normalize_text(research_intent),
    }

    encoded = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


def build_evidence_bundle(
    *,
    documents: list[EvidenceDocument],
    research_intent: str | None,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
    max_evidence_characters: int = MAX_EVIDENCE_CHARACTERS,
) -> EvidenceBundle:
    """
    Build one deterministic, relevance-ranked evidence bundle.

    This performs no network I/O and never invokes an LLM.
    """
    normalized_model_id = model_id.strip()

    if not normalized_model_id:
        raise ValueError("model_id must not be blank")

    normalized_prompt_version = prompt_version.strip()

    if not normalized_prompt_version:
        raise ValueError("prompt_version must not be blank")

    if max_evidence_characters <= 0:
        raise ValueError(
            "max_evidence_characters must be greater than zero"
        )

    usable_documents = [
        document
        for document in documents
        if _normalize_text(document.extracted_text)
    ]
    intent_tokens = _tokens(research_intent)
    ranked_documents = _rank_documents(
        documents=usable_documents,
        intent_tokens=intent_tokens,
    )[:MAX_EVIDENCE_DOCUMENTS]
    character_limits = _character_limits(
        ranked_documents,
        max_evidence_characters=max_evidence_characters,
    )

    header = "\n".join(
        [
            "CAMPAIGN EVIDENCE BUNDLE",
            (
                "Research intent: "
                f"{_normalize_text(research_intent) or 'Not provided'}"
            ),
            (
                "Documents with usable text: "
                f"{len(usable_documents)}"
            ),
            (
                "Distinct evidence sources retained: "
                f"{len(ranked_documents)}"
            ),
            "",
        ]
    )

    parts = [header]
    included_ids: list[UUID] = []
    total_characters = len(header)

    for index, ((document, score), character_limit) in enumerate(
        zip(ranked_documents, character_limits, strict=True),
        start=1,
    ):
        title = _normalize_text(document.title) or "Untitled document"
        prefix = (
            f"[D{index:02d}]\n"
                f"URL: {document.source_url}\n"
            f"Title: {title}\n"
            f"Relevance score: {score}\n"
            "Evidence: "
        )
        separator = "\n\n"
        available_characters = (
            max_evidence_characters
            - total_characters
            - len(separator)
            - len(prefix)
        )

        if available_characters <= 0:
            break

        evidence = _excerpt(
            value=document.extracted_text,
            intent_tokens=intent_tokens,
            max_characters=min(
                character_limit,
                available_characters,
            ),
        )

        if not evidence:
            continue

        card = prefix + evidence
        parts.append(card)
        included_ids.append(document.id)
        total_characters += len(separator) + len(card)

    return EvidenceBundle(
        corpus_fingerprint=_fingerprint(
            documents=documents,
            research_intent=research_intent,
            model_id=normalized_model_id,
            prompt_version=normalized_prompt_version,
        ),
        evidence_text="\n\n".join(parts),
        included_document_ids=tuple(included_ids),
        available_document_count=len(usable_documents),
        input_document_count=len(included_ids),
        input_character_count=total_characters,
    )



def _estimated_document_characters(
    document: EvidenceDocument,
) -> int:
    """
    Estimate each source's potential useful contribution before prompt cards
    are built. This is character-based, not URL-count-based.
    """
    return min(
        len(_normalize_text(document.extracted_text)),
        MAX_CHARACTERS_PER_DOCUMENT,
    )


def _plan_ranked_documents(
    *,
    documents: list[EvidenceDocument],
    research_intent: str | None,
) -> list[tuple[EvidenceDocument, int]]:
    usable_documents = [
        document
        for document in documents
        if _normalize_text(document.extracted_text)
    ]

    return _rank_documents(
        documents=usable_documents,
        intent_tokens=_tokens(research_intent),
    )[:MAX_EVIDENCE_DOCUMENTS]


def _within_total_evidence_budget(
    ranked_documents: list[tuple[EvidenceDocument, int]],
) -> list[EvidenceDocument]:
    selected: list[EvidenceDocument] = []
    used_characters = 0

    for document, _ in ranked_documents:
        estimated_characters = _estimated_document_characters(document)

        if estimated_characters <= 0:
            continue

        if (
            selected
            and used_characters + estimated_characters
            > MAX_TOTAL_MAP_REDUCE_EVIDENCE_CHARACTERS
        ):
            break

        selected.append(document)
        used_characters += estimated_characters

        if used_characters >= MAX_TOTAL_MAP_REDUCE_EVIDENCE_CHARACTERS:
            break

    return selected


def _partition_map_groups(
    documents: list[EvidenceDocument],
) -> list[list[EvidenceDocument]]:
    groups: list[list[EvidenceDocument]] = []
    current_group: list[EvidenceDocument] = []
    current_group_characters = 0

    for document in documents:
        estimated_characters = _estimated_document_characters(document)

        if (
            current_group
            and current_group_characters + estimated_characters
            > MAX_MAP_GROUP_EVIDENCE_CHARACTERS
        ):
            groups.append(current_group)

            if len(groups) >= MAX_MAP_GROUPS:
                break

            current_group = []
            current_group_characters = 0

        current_group.append(document)
        current_group_characters += estimated_characters

    if current_group and len(groups) < MAX_MAP_GROUPS:
        groups.append(current_group)

    return groups[:MAX_MAP_GROUPS]


def build_evidence_plan(
    *,
    documents: list[EvidenceDocument],
    research_intent: str | None,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
) -> EvidencePlan:
    """
    Decide deterministically whether a corpus needs direct synthesis or a
    bounded future map-reduce workflow.

    This function performs no model call. The current API remains unchanged
    until the later orchestration patch consumes EvidencePlan.
    """
    normalized_model_id = model_id.strip()

    if not normalized_model_id:
        raise ValueError("model_id must not be blank")

    normalized_prompt_version = prompt_version.strip()

    if not normalized_prompt_version:
        raise ValueError("prompt_version must not be blank")

    ranked_documents = _plan_ranked_documents(
        documents=documents,
        research_intent=research_intent,
    )
    planned_documents = _within_total_evidence_budget(ranked_documents)
    available_document_count = sum(
        1
        for document in documents
        if _normalize_text(document.extracted_text)
    )
    corpus_fingerprint = _fingerprint(
        documents=documents,
        research_intent=research_intent,
        model_id=normalized_model_id,
        prompt_version=normalized_prompt_version,
    )
    estimated_total_characters = sum(
        _estimated_document_characters(document)
        for document in planned_documents
    )

    if (
        estimated_total_characters
        <= DIRECT_SYNTHESIS_MAX_EVIDENCE_CHARACTERS
    ):
        direct_bundle = build_evidence_bundle(
            documents=planned_documents,
            research_intent=research_intent,
            model_id=normalized_model_id,
            prompt_version=normalized_prompt_version,
            max_evidence_characters=(
                DIRECT_SYNTHESIS_MAX_EVIDENCE_CHARACTERS
            ),
        )

        return EvidencePlan(
            corpus_fingerprint=corpus_fingerprint,
            research_intent=_normalize_text(research_intent),
            direct_bundle=direct_bundle,
            map_groups=(),
            available_document_count=available_document_count,
            input_document_count=(
                direct_bundle.input_document_count
            ),
            input_character_count=(
                direct_bundle.input_character_count
            ),
        )

    groups = _partition_map_groups(planned_documents)
    map_groups = tuple(
        build_evidence_bundle(
            documents=group,
            research_intent=research_intent,
            model_id=normalized_model_id,
            prompt_version=normalized_prompt_version,
            max_evidence_characters=(
                MAX_MAP_GROUP_EVIDENCE_CHARACTERS
            ),
        )
        for group in groups
    )

    return EvidencePlan(
        corpus_fingerprint=corpus_fingerprint,
        research_intent=_normalize_text(research_intent),
        direct_bundle=None,
        map_groups=map_groups,
        available_document_count=available_document_count,
        input_document_count=sum(
            group.input_document_count
            for group in map_groups
        ),
        input_character_count=sum(
            group.input_character_count
            for group in map_groups
        ),
    )
