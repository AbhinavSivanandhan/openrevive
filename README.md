# OpenRevive

OpenRevive is a crawler-first, evidence-grounded research workspace. It turns one approved root URL and a concrete research intent into a bounded campaign with a durable URL frontier, captured source documents, and an explicit source-linked AI brief.

It is not a web-scale crawler, a general chat product, or a complete knowledge-archive platform. The current implementation is a small, inspectable research workflow: campaign intent is durable, crawling runs outside request handlers, raw evidence is retained, and every AI finding links back to a captured document.

```text
Campaign name + root URL + research intent
        |
        v
durable CrawlRun + initial CrawlJob in PostgreSQL
        |
        v
SQS wake-up -> EventBridge Pipe -> finite crawler worker
        |
        +--> bounded HTTP fetch + domain pacing
        +--> raw artifact in MinIO / S3
        +--> extracted document and frontier state in PostgreSQL
        |
        v
Nova Micro frontier selection and explicit campaign brief
        |
        v
campaign workspace: frontier, documents, source-linked findings
```

## What exists today

* A browser control plane that creates and starts a campaign, then opens its dedicated workspace.
* Durable `Workspace`, `Collection`, `CrawlRun`, `CrawlJob`, `CrawledDocument`, `CrawlDomainPolicy`, `WorkerHeartbeat`, and `CampaignBrief` state in PostgreSQL.
* Bounded HTTP/HTTPS campaigns with approved-domain scope, maximum page count, maximum depth, request timeout, and retry budget.
* PostgreSQL job leases with expiry and stale-worker protection.
* Global hostname pacing: at most one active request per hostname, followed by a default one-second cooldown after completion or failure.
* URL normalization and campaign-scoped deduplication.
* Root-page link discovery, deterministic in-scope filtering, and metadata-only Nova Micro selection of a small child frontier.
* Raw page artifacts in MinIO locally or Amazon S3 in AWS, with lightweight HTML title/text extraction in PostgreSQL.
* An explicit campaign brief action after a campaign reaches `SUCCEEDED` or `PARTIALLY_SUCCEEDED`.
* Bounded Nova Micro synthesis with validated source references, fingerprinted caching, and direct or map-reduce execution.
* Local Docker Compose development and an AWS demo deployment using Vercel, ECS Fargate, Aurora PostgreSQL Serverless v2, SQS, EventBridge Pipes, S3, ECR, CloudWatch, Terraform, and Basic Auth.

## What this repository demonstrates

| Concern            | Current implementation                                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Control plane      | FastAPI creates durable campaign intent and never waits for web crawling in the request handler.                                        |
| Durable frontier   | `crawl_jobs` stores every URL-sized work item, retry state, provenance, priority, and fetch metadata.                                   |
| Distributed safety | PostgreSQL row locks, job leases, and shared domain reservations coordinate concurrent workers.                                         |
| Politeness         | One active request per hostname and a cooldown after completion; `robots.txt` is not yet fetched or enforced.                           |
| Evidence           | Raw response bytes are written to object storage; extracted title/text and source metadata are persisted in PostgreSQL.                 |
| AI boundaries      | The model selects only pre-approved candidate IDs and briefs only persisted campaign evidence with validated citations.                 |
| Operations         | Finite cloud workers drain work and exit; verification, inventory, cost, stop, teardown, and destructive cleanup commands are explicit. |

## Technology stack

| Area                        | Technology                                                 |
| --------------------------- | ---------------------------------------------------------- |
| Frontend                    | Next.js 16, React 19, TypeScript, Tailwind                 |
| API / control plane         | Python 3.14, FastAPI, Pydantic Settings                    |
| Data access                 | SQLAlchemy async, asyncpg, Alembic                         |
| Durable state               | PostgreSQL locally; Aurora PostgreSQL Serverless v2 in AWS |
| Local object storage        | MinIO                                                      |
| Production object storage   | Amazon S3                                                  |
| Model calls                 | Amazon Bedrock, Amazon Nova Micro                          |
| Local development           | Docker Compose                                             |
| Production compute          | Amazon ECS Fargate behind an Application Load Balancer     |
| Worker trigger              | Amazon SQS and EventBridge Pipes                           |
| Container registry and logs | Amazon ECR and CloudWatch Logs                             |
| Frontend hosting            | Vercel                                                     |
| Infrastructure              | Terraform and shell lifecycle commands                     |

## Repository layout

```text
apps/web/              Next.js campaign control plane, library, workspace, and document reader
services/api/          FastAPI routes, ORM models, migrations, crawler, and Bedrock workflows
compose.yaml           Local Docker Compose development stack
infra/foundation/      VPC, Aurora, S3, ECR, SQS, IAM, secrets, and budget resources
infra/runtime/         ALB, ECS API, migration/worker tasks, CloudWatch, and EventBridge Pipe
infra/scripts/         Cloud lifecycle, verification, inventory, cost, and auth bootstrap commands
docs/                  Architecture, operations, setup, and product-flow documentation
```

## Quick local start

Prerequisites are Docker Desktop, Git, Node/pnpm, Python/uv, and standard command-line tools. Terraform, AWS CLI, and the Vercel CLI are needed only for the cloud deployment path.

```bash
git clone <repository-url>
cd openrevive

make setup
make verify
make dev-up
```

`make dev-up` keeps Compose logs attached to the terminal. In a second terminal, apply the current schema before using the UI:

```bash
docker compose exec api \
  sh -lc 'uv run --frozen alembic upgrade head'
```

Verify the local stack:

```bash
make dev-ps

curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:3000/api/health/ready
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

Remove local containers and volumes when the data is disposable:

```bash
make dev-reset
```

## Local development commands

```bash
make setup       # bootstrap or verify Mac development tooling
make verify      # verify Docker, Node, pnpm, uv, Terraform, AWS CLI, and related tools
make dev-up      # build and run the local Compose stack in the foreground
make dev-down    # stop local containers and preserve volumes
make dev-reset   # delete local containers and volumes
make dev-logs    # follow local logs
make dev-ps      # show local service status
```

The local stack includes Next.js, FastAPI, PostgreSQL, one continuously polling crawler worker, MinIO, and Redis. Redis is available for future extensions and is not on the active crawl execution path.

## Run tests

The backend tests use the dedicated Compose test database:

```bash
docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen pytest -q'
```

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

`cloud-up` applies the foundation layer, creates or refreshes private-access credentials, builds and publishes an ARM64 container image, applies runtime infrastructure, runs Alembic through a finite ECS migration task, starts the API service, and checks `/health`.

`cloud-down` removes runtime compute and networking while retaining foundation resources such as Aurora, S3, ECR, SQS, IAM, VPC networking, and the budget.

`cloud-nuke` is deliberately destructive. It removes the complete demo environment and its data only when `CONFIRM=DELETE_DEMO_DATA` is supplied.

## Verification commands

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
make cloud-inventory
make cloud-costs
```

* `cloud-check` is read-only. It checks API health, ECS API capacity, Aurora, EventBridge Pipe state, SQS, S3 lifecycle, ECR image availability, AWS Budget configuration, and optionally the authenticated Vercel proxy.
* `cloud-smoke` creates a one-page campaign and proves the deployed crawl path: API → Aurora → SQS → EventBridge Pipe → Fargate worker → external fetch → S3 → Aurora.
* `cloud-inventory` lists AWS resources tagged for the OpenRevive demo.
* `cloud-costs` reports Cost Explorer data by service. It is informational and delayed, not a real-time cost guard.

## Documentation map

| Document                                                             | Purpose                                                                                               |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)                       | Data flow, schema, leases, domain pacing, Bedrock behavior, AWS topology, and correctness boundaries. |
| [`docs/LOCAL_SETUP_MACOS.md`](docs/LOCAL_SETUP_MACOS.md)             | Clone-to-running local setup, migrations, tests, and local troubleshooting.                           |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md)                           | Cloud lifecycle, verification, Basic Auth, troubleshooting, cost controls, and teardown.              |
| [`docs/USER_FLOW_AND_USE_CASES.md`](docs/USER_FLOW_AND_USE_CASES.md) | Current browser workflow, campaign workspace behavior, and suitable bounded research use cases.       |
| [`infra/README.md`](infra/README.md)                                 | Concise AWS infrastructure reference.                                                                 |

## Current boundaries

The current build is intentionally narrow.

* The browser control plane accepts one root URL; its hostname becomes the UI’s allowed-domain scope. The underlying API supports multiple seeds and domains.
* The active HTML discovery path runs only for depth-zero seed jobs. `max_depth` is stored and enforced when child jobs are enqueued, but the worker does not currently recurse from depth-one pages.
* The default UI profile is 50 pages, depth 2, 20-second requests, and two attempts. API validation allows larger bounded values, but this is not a web-scale product.
* Workers process one job lifecycle at a time. Parallelism comes from multiple workers and distinct domains; there is no application-level global worker cap or high-throughput scheduler.
* Domain policy is implemented, but `robots.txt` is not fetched, parsed, or enforced. Existing robots metadata columns are reserved for future work.
* HTML extraction is lightweight. There is no browser JavaScript rendering, PDF pipeline, OCR, image understanding, or authenticated-site connector.
* The campaign brief is explicit and campaign-scoped. There is no general chat interface, vector retrieval, full-text search, or unrestricted answer-generation layer.
* The deployed demo uses Basic Auth as a private-access gate. It does not provide accounts, memberships, tenant isolation, or production authorization.
* The ALB path is demo-grade HTTP infrastructure. A production deployment needs custom-domain TLS, HTTPS-only ingress, stronger identity, monitoring, and abuse controls.


Demo Video : https://www.loom.com/share/9fbaf8f0898d42ceaa99a1b8bd708c11
