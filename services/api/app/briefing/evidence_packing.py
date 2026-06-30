from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID


PROMPT_VERSION = "campaign-brief-v1"
MAX_EVIDENCE_DOCUMENTS = 50
MAX_EVIDENCE_CHARACTERS = 24_000
MAX_CHARACTERS_PER_DOCUMENT = 500

_TOKEN_PATTERN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    id: UUID
    source_url: str
    content_sha256: str
    title: str | None
    extracted_text: str | None


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
    if not intent_tokens:
        return 0

    title_matches = len(
        intent_tokens.intersection(_tokens(document.title))
    )
    body_matches = len(
        intent_tokens.intersection(_tokens(document.extracted_text))
    )

    return title_matches * 3 + body_matches


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
                "id": str(document.id),
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
    Build one deterministic, bounded evidence bundle for a campaign brief.

    The caller makes one later model request with this bundle. This function
    performs no network I/O and never invokes an LLM.
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

    ranked_documents = sorted(
        usable_documents,
        key=lambda document: (
            -_score_document(
                document,
                intent_tokens=intent_tokens,
            ),
            document.source_url,
            str(document.id),
        ),
    )

    selected_documents = ranked_documents[
        :MAX_EVIDENCE_DOCUMENTS
    ]

    header_lines = [
        "CAMPAIGN EVIDENCE BUNDLE",
        f"Research intent: {_normalize_text(research_intent) or 'Not provided'}",
        (
            "Documents with usable text: "
            f"{len(usable_documents)}"
        ),
        "",
    ]

    parts = ["\n".join(header_lines)]
    included_ids: list[UUID] = []
    total_characters = len(parts[0])

    for index, document in enumerate(selected_documents, start=1):
        title = _normalize_text(document.title) or "Untitled document"
        evidence = _normalize_text(document.extracted_text)[
            :MAX_CHARACTERS_PER_DOCUMENT
        ]

        card = (
            f"[D{index:02d}]\n"
            f"Document ID: {document.id}\n"
            f"URL: {document.source_url}\n"
            f"Title: {title}\n"
            f"Evidence: {evidence}"
        )

        separator = "\n\n"
        projected_characters = (
            total_characters + len(separator) + len(card)
        )

        if projected_characters > MAX_EVIDENCE_CHARACTERS:
            break

        parts.append(card)
        included_ids.append(document.id)
        total_characters = projected_characters

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
