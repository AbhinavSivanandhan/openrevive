from app.briefing.bedrock_brief_generator import (
    _REDUCER_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _coverage_checklist,
)


def test_brief_prompts_require_short_refs_and_absence_evidence() -> None:
    for prompt in (_SYSTEM_PROMPT, _REDUCER_SYSTEM_PROMPT):
        assert "distinct parts of the research intent" in prompt
        assert "Never output document UUIDs" in prompt
        assert "source_refs" in prompt
        assert "explicitly establishes absence" in prompt
        assert "Do not invent or strengthen claims" in prompt
        assert "open_questions must be []" in prompt
        assert "recommended_follow_ups must be []" in prompt


def test_coverage_checklist_reserves_requested_facets() -> None:
    checklist = _coverage_checklist(
        "Summarize the article, identify the author's relevant "
        "background, related posts or topics, and any public "
        "professional/contact links."
    )

    assert "Article/content summary" in checklist
    assert "Author background" in checklist
    assert "Related material or topics" in checklist
    assert "Public professional/contact links" in checklist
    assert "mandatory" in checklist
