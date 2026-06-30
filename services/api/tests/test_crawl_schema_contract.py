import app.models  # noqa: F401

from app.db.base import Base


def test_crawl_control_plane_tables_are_registered() -> None:
    assert {
        "crawl_runs",
        "crawl_jobs",
        "crawl_domain_policies",
    } <= set(Base.metadata.tables)



def test_crawl_domain_policy_has_global_pacing_fields() -> None:
    policies = Base.metadata.tables["crawl_domain_policies"]

    assert {
        "domain",
        "robots_txt",
        "robots_fetched_at",
        "robots_http_status",
        "crawl_delay_seconds",
        "next_allowed_at",
        "active_lease_token",
        "active_lease_expires_at",
    } <= set(policies.c.keys())


def test_crawl_run_belongs_to_a_collection() -> None:
    crawl_runs = Base.metadata.tables["crawl_runs"]

    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in crawl_runs.c.collection_id.foreign_keys
    }

    assert foreign_keys == {"collections.id"}


def test_crawl_run_captures_a_configuration_snapshot() -> None:
    crawl_runs = Base.metadata.tables["crawl_runs"]

    assert {
        "seed_urls",
        "allowed_domains",
        "max_pages",
        "max_depth",
        "request_timeout_seconds",
        "max_attempts",
        "idempotency_key",
        "status",
        "started_at",
        "completed_at",
    } <= set(crawl_runs.c.keys())


def test_crawl_job_belongs_to_a_crawl_run() -> None:
    crawl_jobs = Base.metadata.tables["crawl_jobs"]

    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in crawl_jobs.c.crawl_run_id.foreign_keys
    }

    assert foreign_keys == {"crawl_runs.id"}


def test_crawl_job_url_is_unique_within_a_run() -> None:
    crawl_jobs = Base.metadata.tables["crawl_jobs"]

    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in crawl_jobs.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("crawl_run_id", "normalized_url") in unique_constraints


def test_crawl_job_has_leasing_and_observability_fields() -> None:
    crawl_jobs = Base.metadata.tables["crawl_jobs"]

    assert {
        "original_url",
        "normalized_url",
        "domain",
        "depth",
        "status",
        "attempt_count",
        "max_attempts",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "last_error_code",
        "last_error_message",
        "http_status_code",
        "fetched_bytes",
        "fetch_duration_ms",
        "started_at",
        "finished_at",
    } <= set(crawl_jobs.c.keys())


def test_crawl_run_requires_an_idempotency_key() -> None:
    crawl_runs = Base.metadata.tables["crawl_runs"]

    assert crawl_runs.c.idempotency_key.nullable is False


def test_crawl_job_retains_last_claimed_worker_id() -> None:
    crawl_jobs = Base.metadata.tables["crawl_jobs"]

    assert "last_claimed_by_worker_id" in crawl_jobs.c
    assert crawl_jobs.c.last_claimed_by_worker_id.nullable is True


def test_crawled_documents_schema_contract() -> None:
    crawled_documents = Base.metadata.tables["crawled_documents"]

    assert crawled_documents.c["id"].nullable is False
    assert crawled_documents.c["crawl_job_id"].nullable is False
    assert crawled_documents.c["raw_object_key"].nullable is False
    assert crawled_documents.c["content_type"].nullable is False
    assert crawled_documents.c["content_sha256"].nullable is False
    assert crawled_documents.c["title"].nullable is True
    assert crawled_documents.c["extracted_text"].nullable is True
    assert crawled_documents.c["created_at"].nullable is False

    assert any(
        constraint.__class__.__name__ == "UniqueConstraint"
        and tuple(column.name for column in constraint.columns)
        == ("crawl_job_id",)
        for constraint in crawled_documents.constraints
    )
