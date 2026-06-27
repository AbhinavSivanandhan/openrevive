
# OpenRevive Implementation Status

## Product direction

OpenRevive is a crawler-first research intelligence workspace.

A user creates a workspace and collection, starts bounded crawls from approved seed URLs, and later searches and asks questions over captured web knowledge with evidence-backed citations.

## Implemented

### Local platform

- Docker Compose development stack:
  - Next.js web application
  - FastAPI control plane
  - PostgreSQL with pgvector
  - Redis
  - MinIO
- Alembic migration workflow.
- Separate development and isolated test databases.
- Test database uses an isolated connection strategy to avoid cross-event-loop reuse during API and async-worker tests.

### Control plane

- Workspace creation and listing.
- Collection creation and listing within a workspace.
- Crawl-run creation within a collection.
- Required idempotency key for crawl-run creation.
- Seed URL normalization.
- Duplicate normalized seed URL rejection.
- Allowed-domain validation.
- Bounded crawl configuration:
  - maximum pages
  - maximum depth
  - request timeout
  - maximum attempts

### Durable crawler state

- CrawlRun and CrawlJob persistence.
- Job states:
  - PENDING
  - LEASED
  - RETRY_PENDING
  - SUCCEEDED
  - FAILED
- Crawl-run states:
  - PENDING
  - RUNNING
  - SUCCEEDED
  - PARTIALLY_SUCCEEDED
  - FAILED
- Atomic PostgreSQL job leasing with row locking.
- Lease owner, lease token, and lease expiry.
- Safe rejection of stale worker completion and failure reports.
- Immediate retry handling within the configured retry budget.
- Per-job result data:
  - HTTP status
  - fetched bytes
  - fetch duration
  - error code
  - error message

### Verified worker lifecycle primitives

- A worker can claim the oldest eligible job.
- Concurrent workers claim different jobs without duplicate live leases.
- An expired lease can be reclaimed.
- Only the worker holding a valid live lease can complete or fail a job.
- Successful completion clears the lease and persists result metrics.
- Retryable failures return a job to RETRY_PENDING.
- Exhausted retry budgets produce FAILED jobs.
- Runs become SUCCEEDED, PARTIALLY_SUCCEEDED, or FAILED based on terminal job outcomes.

### Current observability foundation

The durable database state can answer:

- How many jobs are pending, leased, retryable, succeeded, or failed?
- Which domains are being crawled?
- Which worker owns each active lease?
- Which leases have expired?
- How many attempts and failures has each job had?
- How many bytes and milliseconds did completed jobs consume?
- Is a crawl run fully successful, partially successful, or failed?

## API contract

FastAPI generates the live API contract from route and Pydantic model definitions.

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

Do not maintain a separate hand-written endpoint catalogue. OpenAPI is the API-shape source of truth.

## In progress

- Runnable worker service that claims, fetches, completes, and fails jobs.
- Crawl-run and job status read endpoints.
- Operational dashboard in the Next.js application.

## Next milestone

A separately runnable worker service that:

1. claims one job with a bounded lease;
2. fetches one HTTP page;
3. records success or failure durably;
4. can run with multiple replicas without duplicate live claims.

## Deliberately deferred

- JavaScript browser rendering.
- Full web link discovery and crawl-depth expansion.
- robots.txt enforcement.
- Per-domain rate limiting.
- Object storage of raw page responses.
- Captured-source and search-index models.
- Agent retrieval and evidence-backed answers.
- Authentication, memberships, and role enforcement.
- Worker heartbeats.
- Prometheus metrics.
- Usage ledger and cost estimation.
- Job cancellation controls.
- File-upload ingestion.

## Verification

Run the isolated API test suite:

    docker compose exec api       sh -c 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen pytest -q'

Check the development stack:

    docker compose ps

Inspect the live API contract:

    curl -fsS http://localhost:8000/openapi.json | python3 -m json.tool
