import app.models  # noqa: F401

from app.db.base import Base


def test_worker_heartbeats_table_is_registered() -> None:
    assert "worker_heartbeats" in Base.metadata.tables


def test_worker_heartbeat_uses_worker_id_as_primary_key() -> None:
    worker_heartbeats = Base.metadata.tables["worker_heartbeats"]

    assert list(worker_heartbeats.primary_key.columns.keys()) == [
        "worker_id"
    ]


def test_worker_heartbeat_has_liveness_and_assignment_fields() -> None:
    worker_heartbeats = Base.metadata.tables["worker_heartbeats"]

    assert {
        "worker_id",
        "status",
        "current_job_id",
        "started_at",
        "last_heartbeat_at",
        "stopped_at",
        "created_at",
        "updated_at",
    } <= set(worker_heartbeats.c.keys())

    assert worker_heartbeats.c.current_job_id.nullable is True


def test_worker_heartbeat_current_job_references_crawl_jobs() -> None:
    worker_heartbeats = Base.metadata.tables["worker_heartbeats"]

    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key
        in worker_heartbeats.c.current_job_id.foreign_keys
    }

    assert foreign_keys == {"crawl_jobs.id"}


def test_worker_heartbeat_has_liveness_query_index() -> None:
    worker_heartbeats = Base.metadata.tables["worker_heartbeats"]

    index_names = {
        index.name
        for index in worker_heartbeats.indexes
    }

    assert (
        "ix_worker_heartbeats_status_last_heartbeat_at"
        in index_names
    )
