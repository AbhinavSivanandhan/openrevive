# OpenRevive Architecture

## 1. Purpose and current system boundary

OpenRevive is a bounded web-research system.

A user creates a campaign with:

* a root URL or API-provided seed URLs,
* an approved domain scope,
* a research intent,
* crawl limits,
* and a retry budget.

The system stores the campaign and its crawl frontier durably, fetches approved HTML pages outside the API request path, preserves raw evidence, and can generate a source-linked research brief from persisted documents.

The current implementation is **not** a web-scale crawler or a general-purpose knowledge archive. It intentionally does not include:

* recursive crawling beyond selected depth-one child pages,
* browser rendering or JavaScript execution,
* PDF, image, video, OCR, or authenticated-site processing,
* robots.txt fetching or enforcement,
* full-text search, vector search, embeddings, or chat over arbitrary content,
* versioned page snapshots, drift detection, alerts, or recovery reports,
* tenant accounts, membership management, or production authorization.

Those concepts appeared in earlier OpenRevive planning. They are not part of the implemented system described here.

## 2. Architectural principles

### PostgreSQL is the source of truth

PostgreSQL stores:

* campaign configuration and lifecycle state,
* the durable URL frontier,
* worker leases,
* retry state,
* global per-domain pacing state,
* document metadata and extracted text,
* campaign-brief cache records,
* and worker heartbeat records.

SQS does not contain crawl jobs. It only wakes cloud workers after a campaign becomes runnable.

### The API does not crawl synchronously

The FastAPI service is the control plane. It records campaign intent, transitions campaign state, exposes reads to the UI, and publishes a wake-up event after the transaction commits.

A worker is the data plane. It claims durable jobs, performs HTTP requests, stores evidence, updates state, and exits after work is drained in AWS.

### The model cannot invent crawl targets or citations

AI is used only inside bounded workflows:

1. **Frontier selection:** Nova Micro chooses from already normalized, in-scope candidate IDs.
2. **Campaign briefing:** Nova Micro receives only persisted evidence cards and must return references to supplied source IDs.

The model never supplies arbitrary URLs, raw SQL, crawler configuration, or unrestricted citations.

### Evidence is stored before crawl success is finalized

For a successful fetch, the worker:

1. writes raw bytes to object storage,
2. persists document metadata and extracted text in PostgreSQL,
3. then marks the crawl job successful.

A job does not become `SUCCEEDED` until document persistence has completed.

## 3. System topology

```text
                                      Browser
                                         |
                                         v
                           Next.js application on Vercel
                           campaign UI + /api/* proxy rewrite
                                         |
                                         v
                           Application Load Balancer
                                         |
                                         v
                    FastAPI API service on ECS Fargate
                    control plane: campaign lifecycle, reads, briefs
                         |                 |                  |
                         |                 |                  |
                         v                 v                  v
              Aurora PostgreSQL       SQS wake-up queue   Amazon Bedrock
           durable campaign state,          |             Nova Micro
           frontier, leases, docs,          |             campaign briefs
           domain pacing, briefs            v
                                      EventBridge Pipe
                                             |
                                             v
                            finite ECS Fargate crawler worker
                         claim jobs, fetch pages, persist evidence
                              |               |              |
                              |               |              |
                              v               v              v
                    Aurora PostgreSQL   S3 raw artifacts   Amazon Bedrock
                    claim/finalize      raw HTML bytes     Nova Micro
                    jobs, store         crawl-runs/...     root-page
                    extracted text                         link selection
                              |
                              v
                     External approved websites
                     bounded HTTP/HTTPS fetches
```

### Main execution paths

```text
Campaign start or resume

Browser
  -> Vercel /api proxy
  -> API service
  -> PostgreSQL: mark campaign RUNNING
  -> SQS wake-up event
  -> EventBridge Pipe
  -> finite worker task
  -> PostgreSQL: claim durable crawl jobs
```

```text
Successful page fetch

Worker
  -> approved external website
  -> S3: store raw response bytes
  -> PostgreSQL: store extracted text and document metadata
  -> PostgreSQL: mark crawl job SUCCEEDED
```

```text
Campaign brief

Browser
  -> API service
  -> PostgreSQL: load persisted campaign evidence
  -> Bedrock / Nova Micro: bounded source-grounded synthesis
  -> PostgreSQL: store validated source-linked brief
  -> Browser: display findings and linked documents
```

The API invokes Bedrock for campaign briefs. Workers invoke Bedrock only when selecting links from an eligible root page.

## 4. Major components

| Component                   | Responsibility                                                                                                                                             |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Next.js web application     | Creates campaigns, stores a local demo collection ID in browser storage, polls campaign state, displays frontier jobs, documents, and campaign briefs.     |
| FastAPI API                 | Creates workspaces, collections, campaigns, seed jobs, lifecycle transitions, campaign reads, document reads, frontier reads, and campaign brief requests. |
| Aurora PostgreSQL           | Durable control-plane state, job frontier, leases, retries, pacing state, document metadata, brief cache, and worker heartbeat records.                    |
| ECS crawler worker          | Claims one durable job at a time, fetches bounded HTML, stores evidence, performs root-page discovery, and completes or retries jobs.                      |
| SQS + EventBridge Pipe      | Starts finite ECS worker tasks after a campaign starts or resumes. It does not own crawl work.                                                             |
| S3                          | Stores raw fetched response bytes using deterministic object keys.                                                                                         |
| Amazon Bedrock / Nova Micro | Selects a bounded child frontier and produces source-linked campaign briefs.                                                                               |
| CloudWatch Logs             | Captures API, worker, and migration-task logs.                                                                                                             |
| Terraform                   | Separates durable foundation resources from disposable runtime resources.                                                                                  |

## 5. Control plane

### Entity hierarchy

```text
Workspace
  └── Collection
        └── CrawlRun
              ├── CrawlJob
              │     └── CrawledDocument
              └── CampaignBrief
```

### Workspace

A workspace is a top-level organizational boundary.

Current API support:

```text
POST /v1/workspaces
GET  /v1/workspaces
```

The browser UI creates one demo workspace on first use and stores the resulting collection ID in `localStorage`. This is a convenience layer for the demo UI, not user authentication or multi-tenant identity.

### Collection

A collection groups related campaigns.

Current API support:

```text
POST /v1/workspaces/{workspace_id}/collections
GET  /v1/workspaces/{workspace_id}/collections
```

Collection names are unique within a workspace.

### Crawl run

A `CrawlRun` is one durable campaign configuration snapshot. It stores:

| Field group        | Stored data                                                |
| ------------------ | ---------------------------------------------------------- |
| Identity           | campaign ID, collection ID, optional name, idempotency key |
| Scope              | seed URLs, allowed domains                                 |
| Research objective | optional research intent                                   |
| Limits             | maximum pages, maximum depth, request timeout, retry count |
| Lifecycle          | status, created time, started time, completed time         |

The API accepts up to 100 seeds and 100 allowed domains. The browser UI currently sends one root URL and derives one allowed hostname from it.

The browser UI currently uses this demo profile:

```text
max_pages:               50
max_depth:               2
request_timeout_seconds: 20
max_attempts:            2
```

The API accepts larger bounded values, including up to 10,000 pages, depth 20, 120-second requests, and 10 attempts. That API capacity should not be interpreted as a claim of web-scale throughput.

### Campaign idempotency

Creating a campaign requires an `Idempotency-Key` request header.

The database enforces:

```text
UNIQUE (collection_id, idempotency_key)
```

A repeated request with the same key and the same normalized request returns the existing campaign. Reusing a key with different campaign data returns a conflict.

This prevents browser retries or network retries from creating duplicate crawl runs.

## 6. Campaign lifecycle

### Campaign states

```text
PENDING
  └── start
        └── RUNNING
              ├── pause  -> PAUSED
              ├── cancel -> CANCELLED
              ├── all jobs succeed -> SUCCEEDED
              └── final failures exist
                    ├── some jobs succeeded -> PARTIALLY_SUCCEEDED
                    └── no jobs succeeded   -> FAILED

PAUSED
  └── resume -> RUNNING or a derived terminal state
```

### Start

```text
POST /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/start
```

A `PENDING` campaign becomes `RUNNING` in a PostgreSQL transaction. After that transaction commits, the API sends an SQS wake-up event when a queue URL is configured.

If queue publication fails, the API returns `503`, but the durable campaign remains `RUNNING`. Retrying start or resume is safe and can publish a later wake-up event.

In local development, no queue URL is configured. The continuously running Compose worker detects the durable work through polling.

### Pause

```text
POST /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/pause
```

Pausing prevents future claims because workers only claim jobs belonging to `RUNNING` campaigns.

A worker that already owns a leased job may still finish its current fetch. It may also persist selected children discovered from that job, allowing a later resume to continue from the durable frontier.

### Resume

```text
POST /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/resume
```

Resume changes a `PAUSED` campaign back to `RUNNING`, unless all of its jobs are already terminal. In that case, the API derives the correct terminal state rather than leaving the campaign running forever.

A successful resume sends another best-effort SQS wake-up event in AWS.

### Cancel

```text
POST /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/cancel
```

Cancellation marks the campaign `CANCELLED` and cancels only jobs that are still `PENDING` or `RETRY_PENDING`.

A job already leased by a worker is not forcibly interrupted. When that worker later records a failure, it becomes `CANCELLED` instead of being requeued.

## 7. Durable crawl frontier

A `CrawlJob` represents one normalized URL fetch.

Important fields include:

| Field                                | Purpose                                                     |
| ------------------------------------ | ----------------------------------------------------------- |
| `crawl_run_id`                       | Campaign ownership                                          |
| `parent_job_id`                      | Discovery provenance for child jobs                         |
| `original_url`                       | Submitted seed URL or retained canonical discovered URL     |
| `normalized_url`                     | Canonical URL used for fetch and deduplication              |
| `domain`                             | Hostname used for global pacing                             |
| `depth`                              | Seed pages are depth `0`; selected children are depth `1`   |
| `priority_score` and `priority_band` | Deterministic or AI-selected frontier ranking               |
| `anchor_text` and `discovery_reason` | Why a child page entered the frontier                       |
| `attempt_count` and `max_attempts`   | Retry budget                                                |
| lease fields                         | Worker ownership and stale-worker protection                |
| fetch metadata                       | HTTP status, bytes, duration, failure code, failure message |

The database enforces:

```text
UNIQUE (crawl_run_id, normalized_url)
```

A URL can appear once per campaign, even when several pages link to it or multiple workers try to enqueue it concurrently.

### Job states

```text
PENDING
  └── LEASED
        ├── SUCCEEDED
        ├── RETRY_PENDING
        │     └── LEASED
        ├── FAILED
        └── CANCELLED
```

`SKIPPED` exists in read models and terminal-state handling for future use, but the current worker does not actively assign that status.

### Frontier ordering

Workers consider candidate jobs in this order:

```text
1. higher priority score
2. lower depth
3. older creation time
4. stable job ID tie-breaker
```

Workers inspect a bounded candidate window of 32 jobs. This allows a worker to skip a paced or currently active domain and claim available work from another domain instead of blocking the entire frontier.

## 8. Job leases and distributed correctness

OpenRevive uses PostgreSQL leases rather than relying on SQS visibility timeouts for crawl correctness.

SQS messages can be duplicated, delayed, or retried. More than one worker task can start for one campaign. The database decides who owns a crawl job.

### Claiming a job

A worker claims a job inside a transaction:

1. reads the database time,
2. finds jobs for `RUNNING` campaigns,
3. accepts `PENDING`, `RETRY_PENDING`, or expired `LEASED` jobs,
4. locks one row with `FOR UPDATE SKIP LOCKED`,
5. assigns a new lease token and expiration,
6. reserves the domain policy row,
7. commits before network I/O begins.

The claim records:

```text
lease_owner
last_claimed_by_worker_id
lease_token
lease_expires_at
```

The default lease duration is 180 seconds.

### Finalizing a job

A worker can complete or fail a job only when all of these remain true:

* the job is still `LEASED`,
* the worker ID matches,
* the lease token matches,
* and the lease has not expired.

This prevents a stale worker from overwriting a result after its lease has expired and another worker has reclaimed the job.

### Retry behavior

A failed job:

* releases its domain reservation,
* records a normalized error code and message,
* returns to `RETRY_PENDING` while attempts remain,
* becomes `FAILED` after the retry budget is exhausted.

There is currently no exponential backoff schedule stored per crawl job. Retry attempts become eligible through the normal claim path after the failure transition.

### Campaign completion

Campaign state is derived from terminal job results:

| Terminal job result                                           | Campaign result       |
| ------------------------------------------------------------- | --------------------- |
| All jobs succeeded                                            | `SUCCEEDED`           |
| At least one job succeeded and at least one exhausted retries | `PARTIALLY_SUCCEEDED` |
| No jobs succeeded and failures exhausted retries              | `FAILED`              |
| Explicit control-plane cancellation                           | `CANCELLED`           |

## 9. Global per-domain pacing

`CrawlDomainPolicy` stores shared crawl behavior for one hostname across every campaign.

This is deliberately global rather than campaign-scoped. Two simultaneous campaigns targeting the same host share the same active-request reservation and cooldown.

Important fields:

```text
domain
active_lease_token
active_lease_expires_at
next_allowed_at
crawl_delay_seconds
robots_txt
robots_fetched_at
robots_http_status
```

Current behavior:

* only one request per hostname may be active at a time,
* a completed or failed request creates a cooldown,
* the default cooldown is one second,
* a job blocked by a domain reservation or cooldown stays pending,
* blocked jobs do not consume retry attempts.

The robots-related columns are reserved for future work. The current worker does **not** fetch, parse, or enforce `robots.txt`.

## 10. HTTP fetch behavior

Each worker process owns one reusable `httpx.AsyncClient`.

The current fetcher:

* uses `GET`,
* sets the user agent to `OpenReviveCrawler/0.1`,
* accepts only `text/html` and `application/xhtml+xml`,
* accepts only HTTP `2xx` responses,
* rejects redirects instead of following them,
* rejects declared or streamed responses over the byte budget,
* disables environment-derived proxy settings with `trust_env=False`,
* records fetch duration and byte count,
* maps timeouts and request exceptions to structured job failures.

The worker default response budget is 4 MB.

The local Docker Compose worker currently overrides this to 2 MB:

```text
WORKER_MAX_RESPONSE_BYTES=2000000
```

The cloud worker uses the application default unless explicitly changed through ECS task configuration.

## 11. URL normalization and scope enforcement

Before a seed or discovered URL becomes a crawl job, OpenRevive normalizes and validates it.

The current logic:

* permits only `http` and `https`,
* rejects URLs with embedded credentials,
* lowercases hostnames,
* removes trailing hostname dots,
* removes fragments,
* removes common tracking parameters such as `utm_*`, `fbclid`, `gclid`, and `ref`,
* preserves meaningful query parameters,
* removes default ports,
* accepts allowed domains and subdomains of allowed domains,
* rejects external domains,
* rejects likely non-HTML assets such as PDFs, images, archives, media, JavaScript, JSON, feeds, and fonts.

The crawler is HTML-only by design. Excluding non-HTML paths prevents the worker from spending crawl capacity on content it cannot meaningfully extract or brief.

## 12. Discovery and frontier selection

### Deterministic discovery

The worker only performs automatic discovery when all conditions are true:

```text
- the fetched job is a depth-zero seed,
- the campaign has a non-empty research intent,
- the response is HTML or XHTML,
- raw artifact persistence succeeded.
```

The worker extracts anchor links from the seed HTML and then:

1. normalizes URLs,
2. filters out external and unsupported targets,
3. removes duplicate path variants,
4. removes documentation chrome and navigation links,
5. scores candidates using overlap between research-intent terms, anchor text, and URL path,
6. retains at most 250 deterministic candidates.

The deterministic score assigns `CORE`, `RELATED`, or `LOW` priority bands before AI selection.

### AI-assisted selection

Nova Micro receives only candidate metadata:

```text
candidate ID
normalized URL
anchor text
research intent
selection target
maximum allowed selections
```

It does not receive page bodies for frontier selection.

The model must return JSON containing only supplied candidate IDs. The application rejects:

* invalid JSON,
* unknown candidate IDs,
* duplicate IDs,
* more IDs than requested,
* malformed model responses.

The worker then converts selected candidates into high-priority child jobs:

```text
priority_band:  SELECTED
priority_score: 1,000,000 downward in model-returned order
```

The current hard limits are:

```text
maximum deterministic candidates: 250
maximum selected children:         12
selection target:                   8
```

The campaign page budget is also enforced, so a seed cannot enqueue more than the remaining campaign capacity.

If model selection fails, the seed still completes successfully. The worker does not fall back to mechanically enqueueing every discovered link.

### Current depth boundary

The UI sends `max_depth: 2`, and the persistence layer respects the campaign depth limit when inserting child jobs.

However, the worker intentionally expands only depth-zero seed jobs. A selected depth-one child page is fetched and stored, but it does not trigger another round of discovery.

The current effective crawl shape is therefore:

```text
seed page at depth 0
  └── selected child pages at depth 1
```

This is a deliberate bounded-research design, not full recursive crawling.

## 13. Evidence persistence

A successful page produces one `CrawledDocument`.

Raw bytes are written first to a deterministic object key:

```text
crawl-runs/{crawl_run_id}/jobs/{crawl_job_id}/raw.html
```

A retry writes to the same key. This avoids creating multiple raw objects for one crawl job.

PostgreSQL stores:

```text
crawl_job_id
raw_object_key
content_type
content_sha256
title
extracted_text
created_at
```

The HTML extractor is dependency-free and intentionally lightweight:

1. prefer text inside `<article>`,
2. otherwise prefer text inside `<main>` or `role="main"`,
3. otherwise use visible text from the page,
4. exclude script, style, navigation, footer, header, forms, sidebars, and common documentation chrome.

The extractor decodes HTML as UTF-8 with replacement for malformed bytes. It does not perform site-specific extraction, browser rendering, or content cleanup beyond this structural filtering.

## 14. Campaign briefs

A campaign brief is an explicit action, not an automatic side effect of crawling.

```text
POST /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/brief
```

Brief generation is permitted only when the campaign is:

```text
SUCCEEDED
PARTIALLY_SUCCEEDED
```

### Evidence plan

The API loads persisted extracted text from successful documents and creates a deterministic evidence plan.

The plan:

* ignores documents without usable extracted text,
* ranks documents using research-intent overlap, title, URL, body text, seed status, frontier priority, and text substance,
* deduplicates documents with the same content hash,
* retains at most 50 distinct evidence sources,
* limits evidence characters and per-document excerpts,
* includes stable source cards such as `D01`, `D02`, and `D03`.

The current prompt version is:

```text
campaign-brief-v7
```

### Direct and map-reduce generation

Small evidence sets use one direct model call.

Larger evidence sets use bounded map-reduce synthesis:

```text
maximum direct evidence:          18,000 characters
maximum map groups:               4
maximum map-group evidence:       12,000 characters each
maximum total Bedrock calls:      5
maximum output tokens per call:   700
```

The model must return structured JSON with one to four source-grounded findings. The application validates that all source references correspond to supplied evidence cards, then replaces short references with persisted document IDs before storing the result.

### Brief caching and retries

`CampaignBrief` is unique for:

```text
(crawl_run_id, corpus_fingerprint)
```

The fingerprint includes the evidence corpus, research intent, model ID, prompt version, source URLs, document identities, content hashes, and crawl provenance.

This gives the following behavior:

| Existing record         | Result                                             |
| ----------------------- | -------------------------------------------------- |
| No matching fingerprint | Create `GENERATING` record and invoke Bedrock.     |
| `GENERATING`            | Return existing record without another model call. |
| `READY`                 | Return cached result.                              |
| `FAILED`                | Retry only after another explicit brief POST.      |

A failed brief is never retried automatically.

## 15. Worker lifecycle and observability

Each worker process has a generated ID based on host, process ID, and random suffix unless `WORKER_ID` is explicitly supplied.

Workers write a durable `WorkerHeartbeat` record with:

```text
worker_id
status
current_job_id
started_at
last_heartbeat_at
stopped_at
```

Worker status transitions are:

```text
STARTING -> IDLE -> PROCESSING -> IDLE -> STOPPED
```

Heartbeat rows are operational visibility metadata. They do not determine job ownership; PostgreSQL lease fields remain authoritative.

### Local worker behavior

The Compose worker runs continuously:

```text
WORKER_EXIT_WHEN_IDLE=false
```

It polls when there is no eligible work.

### Cloud worker behavior

The ECS worker is finite:

```text
WORKER_EXIT_WHEN_IDLE=true
WORKER_IDLE_POLLS_BEFORE_EXIT=2
WORKER_IDLE_POLL_SECONDS=1
```

It exits after two consecutive idle cycles. This avoids paying for an always-on worker service when no campaigns are active.

## 16. Event delivery model

SQS and EventBridge Pipes are used only to start workers.

```text
API commits RUNNING campaign
  |
  v
API sends compact SQS message
  |
  v
EventBridge Pipe starts one ECS worker task
  |
  v
Worker claims durable PostgreSQL jobs
```

The SQS event contains:

```json
{
  "event_type": "crawl.run.wakeup",
  "crawl_run_id": "<campaign-id>"
}
```

The worker does not treat that message as the job payload. It reads eligible work from PostgreSQL.

This allows safe at-least-once delivery:

* a client may retry start or resume,
* SQS may retry delivery,
* EventBridge Pipes may launch more than one worker,
* extra workers may find no eligible work and exit,
* PostgreSQL leases prevent duplicate job ownership.

The current design intentionally does not add a dispatcher service, worker-launch lock, or global worker scheduler.

## 17. Local development topology

```text
Next.js web container
  |
  v
FastAPI API container
  |
  +---------------------+
  |                     |
  v                     v
PostgreSQL          crawler worker
  |                     |
  v                     v
local durable state  MinIO object storage

Redis is also started by Docker Compose.
It is not on the active crawl, queue, cache, or brief-generation path.
```

### Local services

| Service     | Role                                                                                                          |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| PostgreSQL  | Durable application state. The local image includes pgvector, but vector capabilities are not currently used. |
| MinIO       | S3-compatible local raw-artifact storage.                                                                     |
| FastAPI API | Development control plane on port `8000`.                                                                     |
| Worker      | Continuously polling crawler process.                                                                         |
| Next.js web | Development UI on port `3000`.                                                                                |
| Redis       | Reserved for future extensions; currently unused by application code.                                         |

### Local configuration

The API and worker use a direct async PostgreSQL URL:

```text
DATABASE_URL=postgresql+asyncpg://...
```

The worker uses MinIO through explicit S3-compatible configuration:

```text
S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET=<bucket>
S3_ACCESS_KEY_ID=<minio-user>
S3_SECRET_ACCESS_KEY=<minio-password>
S3_REGION_NAME=us-east-1
```

The API receives no object-storage configuration because raw artifact persistence is worker-owned.

## 18. AWS deployment topology

Terraform is split into two layers.

### Foundation layer

`infra/foundation/` creates long-lived resources:

* VPC, internet gateway, route tables, public subnets, and database subnets,
* ALB, API task, worker task, and Aurora security groups,
* Aurora PostgreSQL Serverless v2 cluster and writer,
* Aurora-managed database credential secret,
* private S3 artifact bucket,
* ECR repository and lifecycle policy,
* SQS wake-up queue and dead-letter queue,
* ECS execution and task roles,
* Basic Auth secret,
* optional AWS Budget notifications.

Current demo defaults include:

```text
AWS region:              ap-south-1
Aurora minimum capacity: 0.5 ACU
Aurora maximum capacity: 1.0 ACU
Artifact retention:      14 days
ECR image retention:     5 images
Budget limit:            USD 10, when a budget email is configured
```

### Runtime layer

`infra/runtime/` creates resources that can be removed while preserving database and artifact state:

* ECS cluster,
* Application Load Balancer and HTTP listener,
* API target group,
* one-replica API service,
* API, worker, and migration task definitions,
* CloudWatch log groups,
* EventBridge Pipe and its IAM role.

Current ECS task sizing:

| Task      | CPU |  Memory | Runtime behavior                          |
| --------- | --: | ------: | ----------------------------------------- |
| API       | 512 | 1024 MB | One service replica behind the ALB.       |
| Worker    | 256 |  512 MB | Finite task launched by EventBridge Pipe. |
| Migration | 512 | 1024 MB | Runs Alembic migrations and exits.        |

All ECS tasks use ARM64 Linux Fargate.

## 19. Cloud networking and security

### Network design

Aurora has no public endpoint.

Security-group flow is:

```text
Internet
  |
  v
ALB :80
  |
  v
API task :8000
  |
  v
Aurora PostgreSQL :5432

Worker task
  |
  +--> Aurora PostgreSQL :5432
  +--> S3 / Bedrock / external websites
```

The API accepts inbound traffic only from the ALB security group.

The worker has no inbound rule.

API and worker tasks run in public subnets with public IP assignment. This is a deliberate demo cost decision: it avoids a NAT Gateway while allowing workers to fetch public websites and reach AWS service endpoints.

Security groups still prevent direct inbound traffic to the worker and prevent direct public access to Aurora.

### Credential handling

Local development uses direct database and MinIO credentials.

AWS tasks use IAM roles and Secrets Manager:

* Aurora manages the master database password.
* ECS tasks retrieve that secret through the runtime task role.
* The API constructs the async database URL in process memory from:

  * `DATABASE_SECRET_ARN`,
  * `DATABASE_HOST`,
  * `DATABASE_PORT`,
  * `DATABASE_NAME`.
* S3 and Bedrock use the ECS task role rather than static AWS access keys.
* Basic Auth credentials are stored in Secrets Manager and injected into the API task definition.

The repository does not require production database passwords or AWS access keys in environment files.

### Basic Auth

The deployed demo supports Basic Auth as a private-access gate.

The API protects all routes except:

```text
/health
/health/ready
```

The Next.js application also includes a Vercel proxy that can apply the same Basic Auth environment variables to browser requests.

Basic Auth is appropriate for a private demo. It is not a replacement for production authentication, authorization, tenant isolation, audit logging, or secrets rotation policy.

## 20. Object storage and retention

The AWS artifact bucket:

* blocks public ACLs and public policies,
* uses SSE-S3 encryption,
* expires objects after the configured retention period,
* is accessed only through the ECS task role.

The current bucket configuration is demo-oriented:

* `force_destroy` is enabled,
* object retention is intentionally short,
* no cross-region replication exists,
* no versioning or legal retention policy exists.

Raw artifacts are preserved to support traceability from extracted evidence back to the original fetched response.

## 21. API read model

The web application polls the API rather than receiving push events.

The campaign workspace reads:

```text
GET /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}
GET /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/frontier
GET /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/documents
GET /v1/collections/{collection_id}/crawl-runs/{crawl_run_id}/documents/{document_id}
```

The campaign library reads campaign history through:

```text
GET /v1/collections/{collection_id}/crawl-runs
```

The browser currently refreshes campaign and workspace data every three seconds.

The UI displays durable state from PostgreSQL:

* campaign status,
* job counts,
* frontier ordering and provenance,
* attempts and errors,
* fetched document previews,
* document text,
* and source-linked campaign briefs.

## 22. Operational visibility

OpenRevive exposes:

```text
GET /health
GET /health/ready
```

`/health/ready` verifies a PostgreSQL connection.

CloudWatch receives logs from:

```text
/openrevive/<environment>/api
/openrevive/<environment>/worker
/openrevive/<environment>/migration
```

Operational commands are documented in `docs/OPERATIONS.md`. The primary commands are:

```bash
make cloud-status
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
make cloud-check
make cloud-smoke
make cloud-inventory
make cloud-costs
```

`cloud-smoke` is the strongest end-to-end verification path. It creates a bounded campaign and verifies the deployed flow across the API, Aurora, SQS, EventBridge Pipe, worker, external fetch, S3, and persisted campaign state.

## 23. Cost and lifecycle model

The cloud design prioritizes explicit teardown and low idle cost.

| Control              | Purpose                                                                    |
| -------------------- | -------------------------------------------------------------------------- |
| One API replica      | Keeps steady-state ECS cost bounded.                                       |
| Finite workers       | Workers exit after draining work rather than remaining permanently active. |
| Aurora capacity cap  | Limits Aurora Serverless v2 scaling.                                       |
| S3 lifecycle policy  | Deletes demo artifacts after 14 days.                                      |
| ECR lifecycle policy | Retains only five images.                                                  |
| AWS Budgets          | Provides delayed cost alerts when configured.                              |
| `cloud-kill`         | Stops running compute and worker launches.                                 |
| `cloud-down`         | Removes runtime infrastructure while retaining foundation state.           |
| `cloud-nuke`         | Removes the complete demo environment only with explicit confirmation.     |

Cost Explorer and AWS Budgets are delayed reporting mechanisms. They are monitoring controls, not immediate billing cutoffs.

## 24. Current limitations and production hardening

The current deployment is intentionally demo-grade.

A production version should add:

* HTTPS-only ingress with a custom domain and TLS certificate,
* real user authentication and authorization,
* workspace membership and tenant isolation,
* robots.txt fetching and enforcement,
* explicit crawl-delay handling from robots directives,
* per-user and per-domain quotas,
* abuse prevention and request validation beyond the demo boundary,
* browser rendering for JavaScript-heavy sites,
* richer extraction for PDFs and non-HTML documents,
* more durable retry scheduling and backoff,
* worker-launch coordination or a scheduler for high concurrency,
* dashboards, metrics, traces, and alerting,
* stronger database backup and retention policies,
* secret rotation procedures,
* immutable artifact retention when required,
* search and retrieval features only after their operational and security model is defined.

## 25. Design summary

OpenRevive uses a simple separation of responsibilities:

```text
FastAPI records intent.
PostgreSQL owns durable state and correctness.
SQS wakes workers.
Fargate performs bounded network work.
S3 preserves raw evidence.
Bedrock selects from bounded inputs and returns validated source links.
The UI reads durable campaign state.
```

That separation is the core of the implementation. The system can tolerate duplicate wake-up events and multiple workers because correctness is enforced by database-backed job leases, URL uniqueness, campaign budgets, and global domain reservations rather than by assuming a single worker or exactly-once message delivery.
