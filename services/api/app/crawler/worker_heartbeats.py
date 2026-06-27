from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.worker_heartbeat import WorkerHeartbeat

HEARTBEAT_STATUSES = {
    "STARTING",
    "IDLE",
    "PROCESSING",
}


class WorkerNotRegisteredError(RuntimeError):
    """Raised when a worker reports state before registering."""


def normalize_worker_id(worker_id: str) -> str:
    normalized_worker_id = worker_id.strip()

    if not normalized_worker_id:
        raise ValueError("worker_id must not be blank")

    if len(normalized_worker_id) > 128:
        raise ValueError("worker_id must be at most 128 characters")

    return normalized_worker_id


def normalize_heartbeat_status(status: str) -> str:
    normalized_status = status.strip().upper()

    if normalized_status not in HEARTBEAT_STATUSES:
        allowed_statuses = ", ".join(sorted(HEARTBEAT_STATUSES))
        raise ValueError(
            f"status must be one of: {allowed_statuses}"
        )

    return normalized_status


async def get_database_now(session: AsyncSession) -> datetime:
    database_now = await session.scalar(select(func.now()))

    if not isinstance(database_now, datetime):
        raise RuntimeError("database did not return a timestamp")

    return database_now


async def register_worker(
    session: AsyncSession,
    *,
    worker_id: str,
) -> WorkerHeartbeat:
    """
    Register a worker process or begin a new lifetime after clean shutdown.

    Re-registering an active worker is idempotent: it refreshes liveness
    without resetting status, current job assignment, or started_at.
    """
    normalized_worker_id = normalize_worker_id(worker_id)

    async with session.begin():
        database_now = await get_database_now(session)

        heartbeat = await session.scalar(
            select(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.worker_id == normalized_worker_id
            )
            .with_for_update()
        )

        if heartbeat is None:
            heartbeat = WorkerHeartbeat(
                worker_id=normalized_worker_id,
                status="STARTING",
                current_job_id=None,
                started_at=database_now,
                last_heartbeat_at=database_now,
                stopped_at=None,
            )
            session.add(heartbeat)
        elif heartbeat.status == "STOPPED":
            heartbeat.status = "STARTING"
            heartbeat.current_job_id = None
            heartbeat.started_at = database_now
            heartbeat.last_heartbeat_at = database_now
            heartbeat.stopped_at = None
            heartbeat.updated_at = database_now
        else:
            # This is still the same active worker process.
            # Preserve lifecycle and assignment state; only refresh liveness.
            heartbeat.last_heartbeat_at = database_now
            heartbeat.updated_at = database_now

        await session.flush()

    return heartbeat


async def record_heartbeat(
    session: AsyncSession,
    *,
    worker_id: str,
    status: str,
    current_job_id: UUID | None = None,
) -> WorkerHeartbeat:
    """
    Record current worker liveness and optional active-job assignment.

    Lease ownership remains authoritative. current_job_id exists for
    operational visibility only.
    """
    normalized_worker_id = normalize_worker_id(worker_id)
    normalized_status = normalize_heartbeat_status(status)

    if normalized_status == "PROCESSING" and current_job_id is None:
        raise ValueError(
            "PROCESSING status requires current_job_id"
        )

    if normalized_status != "PROCESSING" and current_job_id is not None:
        raise ValueError(
            "only PROCESSING status may include current_job_id"
        )

    async with session.begin():
        database_now = await get_database_now(session)

        heartbeat = await session.scalar(
            select(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.worker_id == normalized_worker_id
            )
            .with_for_update()
        )

        if heartbeat is None:
            raise WorkerNotRegisteredError(
                f"worker is not registered: {normalized_worker_id}"
            )

        heartbeat.status = normalized_status
        heartbeat.current_job_id = current_job_id
        heartbeat.last_heartbeat_at = database_now
        heartbeat.stopped_at = None
        heartbeat.updated_at = database_now

        await session.flush()

    return heartbeat


async def stop_worker(
    session: AsyncSession,
    *,
    worker_id: str,
) -> WorkerHeartbeat:
    """Mark a worker process as cleanly stopped."""
    normalized_worker_id = normalize_worker_id(worker_id)

    async with session.begin():
        database_now = await get_database_now(session)

        heartbeat = await session.scalar(
            select(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.worker_id == normalized_worker_id
            )
            .with_for_update()
        )

        if heartbeat is None:
            raise WorkerNotRegisteredError(
                f"worker is not registered: {normalized_worker_id}"
            )

        heartbeat.status = "STOPPED"
        heartbeat.current_job_id = None
        heartbeat.last_heartbeat_at = database_now
        heartbeat.stopped_at = database_now
        heartbeat.updated_at = database_now

        await session.flush()

    return heartbeat
