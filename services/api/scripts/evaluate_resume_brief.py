from __future__ import annotations

import asyncio
import json
from uuid import UUID

from app.briefing.bedrock_brief_generator import (
    generate_campaign_brief,
)
from app.briefing.evidence_packing import (
    EvidenceDocument,
    build_evidence_bundle,
)

MODEL_ID = "apac.amazon.nova-micro-v1:0"
REGION_NAME = "ap-south-1"

DOCUMENTS = [
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        source_url=(
            "https://sarthakdalal28.github.io/posts/"
            "2025-11-01-resume-tips"
        ),
        content_sha256="1" * 64,
        title="Resume tips that got me my job at Google",
        extracted_text=(
            "Sarthak Dalal describes iterating on his resume until it led "
            "to interviews with Google and Amazon, an online assessment from "
            "Meta, and traction with other companies. He tailors bullet "
            "points and projects to the job description, uses a clean "
            "single-column format, and emphasizes recruiter pattern matching."
        ),
        depth=0,
        priority_score=1_000_001,
        priority_band="HIGH",
    ),
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000002"),
        source_url="https://sarthakdalal28.github.io/",
        content_sha256="2" * 64,
        title="About Me - Sarthak Dalal",
        extracted_text=(
            "Sarthak Dalal is a Software Engineer at Google. He works on a "
            "Gemini-powered chatbot for Google Analytics. His background "
            "includes an MS in Computer Science from Rutgers University and "
            "a BE in Electronics and Telecommunication from the University "
            "of Mumbai."
        ),
        depth=1,
        priority_score=1_000_000,
        priority_band="SELECTED",
    ),
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000003"),
        source_url="https://sarthakdalal28.github.io/experience/",
        content_sha256="3" * 64,
        title="My Experience - Sarthak Dalal",
        extracted_text=(
            "At Google, Sarthak developed a real-time Analytics Advisor "
            "Metrics Dashboard for a Gemini-powered chatbot and worked on a "
            "Feature Extractor module that processes user-query data."
        ),
        depth=1,
        priority_score=999_999,
        priority_band="SELECTED",
    ),
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000004"),
        source_url="https://sarthakdalal28.github.io/posts/",
        content_sha256="4" * 64,
        title="Blog posts - Sarthak Dalal",
        extracted_text=(
            "The blog index includes the resume-tips article, published "
            "November 1, 2025, about the author's job-search and resume "
            "iteration process."
        ),
        depth=1,
        priority_score=999_998,
        priority_band="SELECTED",
    ),
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000005"),
        source_url="https://sarthakdalal28.github.io/projects/",
        content_sha256="5" * 64,
        title="Projects - Sarthak Dalal",
        extracted_text=(
            "A featured project is Payment Detail Register, a single-page "
            "application using C#, .NET Core, Angular, and SQL with "
            "validation and CRUD functionality."
        ),
        depth=1,
        priority_score=999_997,
        priority_band="SELECTED",
    ),
    EvidenceDocument(
        id=UUID("10000000-0000-0000-0000-000000000006"),
        source_url="https://sarthakdalal28.github.io/contact/",
        content_sha256="6" * 64,
        title="Contact Me - Sarthak Dalal",
        extracted_text=(
            "The crawled Contact Me page exposed only its page heading. "
            "No email address, phone number, or public social-profile URL "
            "appeared in the extracted text."
        ),
        depth=1,
        priority_score=999_996,
        priority_band="SELECTED",
    ),
]

SOURCE_GROUPS = {
    "article": {"10000000-0000-0000-0000-000000000001"},
    "author_background": {
        "10000000-0000-0000-0000-000000000002",
        "10000000-0000-0000-0000-000000000003",
    },
    "related_or_extra": {
        "10000000-0000-0000-0000-000000000004",
        "10000000-0000-0000-0000-000000000005",
    },
    "contact": {"10000000-0000-0000-0000-000000000006"},
}


async def main() -> None:
    bundle = build_evidence_bundle(
        documents=DOCUMENTS,
        research_intent=(
            "Summarize the article, identify the author's relevant "
            "background, related posts or topics, and any public "
            "professional/contact links."
        ),
        model_id=MODEL_ID,
    )

    print("===== packed evidence sent to Nova =====")
    print(bundle.evidence_text)
    print("===== end packed evidence =====\n")

    result = await generate_campaign_brief(
        evidence_bundle=bundle,
        model_id=MODEL_ID,
        region_name=REGION_NAME,
    )

    brief = result.brief_json
    cited_ids = {
        source_id
        for finding in brief["key_findings"]
        for source_id in finding["source_document_ids"]
    }

    print("===== model output =====")
    print(json.dumps(brief, indent=2))

    print("\n===== source-group coverage =====")
    for group_name, group_ids in SOURCE_GROUPS.items():
        print(
            f"{group_name}: "
            f"{'USED' if cited_ids.intersection(group_ids) else 'MISSING'}"
        )

    print("\n===== evaluator facts =====")
    print(f"evidence_documents={bundle.input_document_count}")
    print(f"evidence_characters={bundle.input_character_count}")
    print(f"output_tokens={result.output_token_count}")
    print(f"cited_source_count={len(cited_ids)}")


asyncio.run(main())
