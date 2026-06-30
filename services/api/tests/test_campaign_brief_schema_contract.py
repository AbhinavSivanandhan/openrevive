import app.models  # noqa: F401

from app.db.base import Base


def test_campaign_briefs_schema_contract() -> None:
    briefs = Base.metadata.tables["campaign_briefs"]

    assert {
        "id",
        "crawl_run_id",
        "corpus_fingerprint",
        "model_id",
        "prompt_version",
        "status",
        "input_document_count",
        "input_character_count",
        "output_token_count",
        "brief_json",
        "error_code",
        "error_message",
        "completed_at",
        "created_at",
        "updated_at",
    } <= set(briefs.c.keys())

    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in briefs.c.crawl_run_id.foreign_keys
    }

    assert foreign_keys == {"crawl_runs.id"}

    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in briefs.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert (
        "crawl_run_id",
        "corpus_fingerprint",
    ) in unique_constraints
