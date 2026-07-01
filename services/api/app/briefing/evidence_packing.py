from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID


PROMPT_VERSION = "campaign-brief-v2"
MAX_EVIDENCE_DOCUMENTS = 50
MAX_EVIDENCE_CHARACTERS = 24_000
MIN_CHARACTERS_PER_DOCUMENT = 160
MAX_CHARACTERS_PER_DOCUMENT = 3_600

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
) -> list[int]:
    if not ranked_documents:
        return []

    estimated_metadata_characters = (
        600 + len(ranked_documents) * 220
    )
    text_budget = max(
        0,
        MAX_EVIDENCE_CHARACTERS - estimated_metadata_characters,
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
    character_limits = _character_limits(ranked_documents)

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
            f"Document ID: {document.id}\n"
            f"URL: {document.source_url}\n"
            f"Title: {title}\n"
            f"Relevance score: {score}\n"
            "Evidence: "
        )
        separator = "\n\n"
        available_characters = (
            MAX_EVIDENCE_CHARACTERS
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
