
# OpenRevive Architecture

## Current topology

Browser
  -> Next.js web application
  -> FastAPI control plane
  -> PostgreSQL

Redis and MinIO are available in the local Docker Compose environment. They are not yet on the active crawler execution path.

## Service boundaries

The API is the control plane. It records durable intent and system state. It must not crawl URLs synchronously.

The future worker service is the data plane. It claims durable jobs, performs bounded network work, and records results through the crawler lifecycle primitives.

## Durable model

Workspace
  -> Collection
  -> CrawlRun
  -> CrawlJob
  -> Worker execution

A CrawlRun stores a historical configuration snapshot:

- seed URLs
- allowed domains
- maximum pages
- maximum depth
- request timeout
- maximum attempts
- idempotency key

A CrawlJob stores one unit of durable work:

- original and normalized URLs
- domain and depth
- status and attempt count
- lease owner, token, and expiry
- error code and message
- HTTP status
- fetched bytes
- fetch duration
- execution timestamps

## Job lifecycle

PENDING
  -> LEASED
     -> SUCCEEDED
     -> RETRY_PENDING
        -> LEASED
     -> FAILED

A worker must own a live lease defined by:

- lease_owner
- lease_token
- lease_expires_at

A stale worker cannot complete or fail a job after another worker has reclaimed it.

## Crawl-run lifecycle

PENDING
  -> RUNNING
     -> SUCCEEDED
     -> PARTIALLY_SUCCEEDED
     -> FAILED

A run is SUCCEEDED only when all jobs succeed.

A run is PARTIALLY_SUCCEEDED when all jobs are terminal, at least one job succeeds, and at least one job fails.

A run is FAILED when all jobs are terminal and none succeed.

## Distributed execution model

Workers claim jobs through PostgreSQL row locking with SKIP LOCKED.

Multiple workers can attempt to claim work concurrently. A row lock prevents two workers from receiving the same live lease for the same job.

Expired leases make abandoned work eligible for recovery by another worker.

## Observability model

Operational state is recorded with the work rather than inferred from logs alone.

Current durable observability includes:

- queue depth by job status;
- jobs grouped by crawl run;
- jobs grouped by domain;
- active leases grouped by worker ID;
- expired lease detection;
- retry and failure counts;
- HTTP status, bytes, and fetch duration;
- run-level terminal outcome.

Future observability additions:

- worker heartbeats and capacity;
- append-only crawl events;
- status and metrics endpoints;
- Prometheus-compatible metrics;
- usage ledger for requests, bytes, storage, and compute time;
- cost estimation based on measured usage;
- cancellation and reconciliation controls.

## Documentation ownership

- OpenAPI is the source of truth for endpoint shapes.
- docs/IMPLEMENTATION_STATUS.md is the source of truth for delivered work, active work, and deliberate deferrals.
- docs/ARCHITECTURE.md is the source of truth for durable state, service boundaries, and operational design.
