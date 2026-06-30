# OpenRevive

OpenRevive is a crawl-first research workspace for running bounded web research campaigns and retaining the resulting evidence.

A user creates a workspace, groups work into collections, starts a campaign from approved seed URLs, watches the durable crawl frontier update live, and reads the captured documents after the run completes.

The deployed system is deliberately split into a control plane and a worker data plane:

```text
Vercel / Next.js UI
        |
        v
AWS ALB -> ECS Fargate API -> Aurora PostgreSQL
                            |
                            +-> SQS -> EventBridge Pipe -> one-shot Fargate worker
                                                              |
                                                              +-> S3 raw artifacts
```

The API records durable campaign intent. It does not crawl pages synchronously. Starting or resuming a campaign publishes a wake-up event to SQS; EventBridge Pipes launches a worker task; the worker claims PostgreSQL-backed jobs, fetches pages, discovers in-scope links, writes raw evidence to S3, persists extracted documents, and exits after it drains work.

## What it demonstrates

- Durable campaign, crawl-run, crawl-job, and document state.
- PostgreSQL leasing with expiry and safe recovery of abandoned work.
- Bounded crawling by seed URL, domain allow-list, maximum depth, maximum page count, request timeout, and retry budget.
- Link discovery and prioritised frontier expansion.
- Persisted raw artifacts in S3 and extracted text in PostgreSQL.
- Live campaign status, frontier state, and document evidence in a Next.js UI.
- Event-driven worker orchestration through SQS and EventBridge Pipes.
- On-demand ECS Fargate workers that drain work and exit instead of remaining permanently active.
- Repeatable deployment checks and end-to-end infrastructure smoke tests.

## Technology stack

| Area | Technology |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind |
| API / control plane | Python 3.14, FastAPI, Pydantic Settings |
| Data access | SQLAlchemy async, asyncpg, Alembic |
| Durable state | PostgreSQL locally; Aurora PostgreSQL Serverless v2 in AWS |
| Local object storage | MinIO |
| Production object storage | Amazon S3 |
| Local development | Docker Compose |
| Production API | Amazon ECS Fargate behind an Application Load Balancer |
| Worker trigger | Amazon SQS and EventBridge Pipes |
| Container registry | Amazon ECR |
| Logs | Amazon CloudWatch Logs |
| Frontend hosting | Vercel |
| Infrastructure | Terraform and shell lifecycle commands |

## Repository layout

```text
apps/web/              Next.js campaign workspace UI
services/api/          FastAPI API, ORM models, migrations, crawler worker
compose.yaml           Local Docker Compose development stack
infra/foundation/      AWS VPC, Aurora, S3, ECR, SQS, IAM, budget
infra/runtime/         ALB, ECS API service, task definitions, EventBridge Pipe
infra/scripts/         Cloud lifecycle, verification, inventory, and cost commands
docs/                  Architecture, operations, user flows, and Mac setup
```

## Quick local start

Prerequisites are Docker Desktop, Git, Node/pnpm, Python/uv, and standard command-line tools.

```bash
git clone <repository-url>
cd openrevive

make setup
make verify
make dev-up
```

Open:

```text
UI:          http://localhost:3000
API:         http://localhost:8000
API docs:    http://localhost:8000/docs
MinIO:       http://localhost:9001
```

Stop the local stack while preserving Docker volumes:

```bash
make dev-down
```

Remove local containers and volumes when data is disposable:

```bash
make dev-reset
```

See `docs/LOCAL_SETUP_MACOS.md` for the full first-time MacBook setup.

## Local development commands

```bash
make setup       # install or verify local development tooling
make verify      # verify Docker, Node, pnpm, uv, Terraform, AWS CLI, etc.
make dev-up      # build and run the local Compose stack
make dev-down    # stop local containers, preserve volumes
make dev-reset   # delete local containers and volumes
make dev-logs    # follow local logs
make dev-ps      # show local service status
```

The local stack includes Next.js, FastAPI, PostgreSQL, one continuously polling crawler worker, MinIO, and Redis. Redis is available for future extensions but is not on the active crawl execution path.

## AWS deployment commands

```bash
make cloud-up
make cloud-status
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
make cloud-stop
make cloud-resume
make cloud-kill
make cloud-down
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

`cloud-up` applies the AWS foundation, builds and publishes the container image, runs Alembic migrations through an ECS task, starts the API service, and verifies API health.

`cloud-down` removes runtime compute and load-balancing resources while retaining foundation resources such as Aurora, S3, ECR, SQS, IAM, and the budget.

`cloud-nuke` is deliberately destructive. It removes the entire demo environment and its data only when `CONFIRM=DELETE_DEMO_DATA` is supplied.

## Verification commands

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
make cloud-inventory
make cloud-costs
```

- `cloud-check` is read-only. It verifies API health, ECS capacity, Aurora, EventBridge Pipe state, SQS, S3 lifecycle, ECR image availability, AWS Budget configuration, and optionally the Vercel API proxy.
- `cloud-smoke` first runs `cloud-check`, then creates a one-page campaign and proves the complete path: API -> Aurora -> SQS -> Pipe -> Fargate worker -> S3 -> Aurora.
- `cloud-inventory` lists AWS resources tagged for the OpenRevive demo.
- `cloud-costs` reports current-month account costs by service. Cost Explorer is delayed and is informational rather than a real-time cost guard.

## Documentation map

| Document | Purpose |
| --- | --- |
| `docs/ARCHITECTURE.md` | Durable model, event-driven execution path, AWS topology, correctness boundaries |
| `docs/OPERATIONS.md` | Cloud lifecycle, smoke tests, logs, troubleshooting, teardown |
| `docs/USER_FLOW_AND_USE_CASES.md` | Product workflow, personas, and example campaigns |
| `docs/LOCAL_SETUP_MACOS.md` | Clone-to-running local setup on a MacBook |
| `infra/README.md` | Concise infrastructure-specific command reference |

## Current boundaries

OpenRevive is a working crawler and research-campaign control plane, not yet a finished general-purpose research product. Current deliberate boundaries include:

- No browser JavaScript rendering.
- No robots.txt enforcement or per-domain politeness controls.
- No authentication, memberships, or tenant isolation.
- No full-text search, vector retrieval, or answer-generation layer.
- No hard global worker-concurrency cap beyond bounded campaign work and the deployment’s cost controls.
- No production-grade TLS/custom-domain setup for the demo ALB path.

These are deliberate next-stage concerns, not hidden assumptions in the current crawler model.
