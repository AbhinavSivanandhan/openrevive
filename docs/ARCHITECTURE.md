# OpenRevive Architecture

## Purpose

OpenRevive separates durable research intent from crawler execution.

The API is the **control plane**. It creates and transitions campaign state, persists crawl configuration, exposes the UI/API, and emits a wake-up event only after a crawl run is committed as `RUNNING`.

The crawler is the **data plane**. It is an ephemeral Fargate task that claims durable jobs from PostgreSQL, performs bounded network work, writes evidence, updates durable state, and exits after it drains work.

```text
                    control plane
Browser -> Vercel -> Next.js -> ALB -> ECS API -> Aurora PostgreSQL
                                  |
                                  | crawl run committed as RUNNING
                                  v
                                 SQS
                                  |
                                  v
                           EventBridge Pipe
                                  |
                                  v
                           Fargate worker task
                                  |
                     +------------+------------+
                     v                         v
             Aurora job/document state      S3 raw artifacts
```

## Core entities

```text
Workspace
  -> Collection
      -> CrawlRun / campaign
          -> CrawlJob
              -> CrawledDocument
              -> S3 raw artifact
```

### Workspace

A top-level research boundary. It owns collections.

### Collection

A named grouping of related campaigns and captured evidence.

### Crawl run / campaign

A durable snapshot of one crawl request. It stores the seed URLs, allowed domains, research intent, maximum pages, maximum depth, request timeout, retry budget, and idempotency key.

A campaign has user-visible lifecycle state such as `PENDING`, `RUNNING`, and terminal outcomes including `SUCCEEDED`, `PARTIALLY_SUCCEEDED`, `FAILED`, or `CANCELLED`.

### Crawl job

One durable URL-fetch unit. Jobs track normalized URLs, discovery depth, priority, retries, lease ownership, HTTP outcome, byte count, duration, and failures.

### Crawled document

A persisted extracted result associated with a successful job. It includes source URL, title, content type, preview text, creation time, and the S3 key for the raw page evidence.

## Crawl job correctness model

The queue of crawl work is PostgreSQL-backed.

A worker claims eligible work through a transaction and row locking. The claimed job receives:

```text
lease_owner
lease_token
lease_expires_at
```

A worker can complete or fail a job only when its lease token is still valid. That prevents a stale worker from overwriting the result after a lease has expired and another worker has reclaimed the job.

The relevant job states are:

```text
PENDING
  -> LEASED
      -> SUCCEEDED
      -> RETRY_PENDING
          -> LEASED
      -> FAILED
```

The run outcome is derived from its jobs:

```text
all terminal jobs succeeded
  -> SUCCEEDED

some terminal jobs succeeded and some failed
  -> PARTIALLY_SUCCEEDED

all terminal jobs failed
  -> FAILED
```

## Event delivery model

SQS is a **wake-up mechanism**, not the source of truth for crawl work.

When a campaign changes to `RUNNING`, the API publishes a compact crawl wake-up event. EventBridge Pipes consumes the event and calls ECS `RunTask` for the worker task definition.

This delivery can be at-least-once:

- a client can retry a start/resume request;
- SQS delivery can retry;
- more than one wake-up can lead to more than one worker task.

Correctness does not rely on exactly one worker launch. PostgreSQL leases decide which worker owns each job. Extra workers find no eligible work after the queue is drained and exit after two idle polls.

This is intentionally simpler than adding a dispatcher service, worker-launch lock table, or scheduler for the demo deployment.

## Campaign execution flow

```text
1. User creates workspace and collection.
2. User creates a bounded campaign from seed URLs.
3. API creates CrawlRun and initial CrawlJob records in Aurora.
4. User starts the campaign.
5. API commits RUNNING state.
6. API sends a wake-up message to SQS.
7. EventBridge Pipe launches one Fargate worker task.
8. Worker leases a job from Aurora.
9. Worker fetches the page and evaluates scope/discovery rules.
10. Worker stores the raw response in S3.
11. Worker persists extracted document and job outcome in Aurora.
12. Worker repeats until no eligible work remains.
13. Worker drains after two idle polls and exits.
14. API/UI read durable Aurora state to show frontier and results.
```

## Local architecture

Docker Compose is used only for local development:

```text
Next.js web
  -> FastAPI API
  -> PostgreSQL
  -> crawler worker
  -> MinIO for S3-compatible local artifacts

Redis is available for future extensions.
It is not on the active crawler execution path.
```

The local worker polls continuously because it is a long-running development process. The cloud worker uses the same crawler code with `WORKER_EXIT_WHEN_IDLE=true`.

## AWS deployment topology

```text
Internet
  |
  v
Vercel-hosted Next.js frontend
  |
  | /api/* rewrite
  v
Application Load Balancer
  |
  v
ECS Fargate API service
  |
  +--> Aurora PostgreSQL Serverless v2
  +--> SQS crawl-event queue
  +--> S3 artifact bucket
  |
  v
EventBridge Pipe
  |
  v
ECS Fargate worker task
  |
  +--> Aurora PostgreSQL Serverless v2
  +--> S3 artifact bucket
```

### Foundation layer

`infra/foundation/` creates the slower-changing resources:

- VPC, public subnets, database subnets, route table, internet gateway.
- Security groups for ALB, API task, worker task, and Aurora.
- Aurora PostgreSQL Serverless v2 cluster and writer.
- Aurora-managed database credential secret in AWS Secrets Manager.
- S3 artifact bucket with public access blocked, SSE-S3 encryption, and a 14-day lifecycle expiry.
- ECR repository with image-retention policy.
- SQS crawl wake-up queue and dead-letter queue.
- ECS execution role and runtime task role.
- AWS Budget notifications.

### Runtime layer

`infra/runtime/` creates resources that can be destroyed and recreated while retaining durable foundation state:

- ECS cluster.
- Application Load Balancer, target group, and HTTP listener.
- ECS API task definition and one-replica API service.
- ECS migration task definition.
- ECS finite worker task definition.
- CloudWatch log groups for API, worker, and migration tasks.
- EventBridge Pipe and its dedicated IAM role.

## Network and security posture

Aurora has no public endpoint. Its security group permits PostgreSQL traffic only from the API-task and worker-task security groups.

The API task accepts traffic only from the ALB security group. The worker task has no inbound rule.

For the demo deployment, API and worker tasks use public subnets with public IP assignment so they can fetch external websites and reach AWS services without NAT Gateway cost. Public reachability is still constrained by security groups: the API accepts inbound traffic only through the ALB, and the worker accepts none.

Database credentials are managed by Aurora and retrieved through AWS Secrets Manager using the ECS task role. The repository does not contain production database credentials.

## Observability

OpenRevive treats durable state as the primary operational record.

The UI and API expose campaign state, job counts, frontier status, documents, and failure context from PostgreSQL. CloudWatch captures API, worker, and migration logs.

Operational commands include:

```bash
make cloud-status
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
make cloud-check
make cloud-smoke
make cloud-inventory
make cloud-costs
```

`cloud-smoke` is the most complete integration proof. It creates a bounded one-page campaign, waits for completion, confirms a persisted S3 artifact, and confirms the worker drain log.

## Cost controls and teardown

The demo is designed for explicit cost containment:

- Aurora Serverless v2 is capped at the configured maximum capacity.
- One API service replica is used.
- Workers are finite tasks rather than always-on services.
- S3 artifacts expire after 14 days.
- ECR retains only a small number of images.
- AWS Budget alerts are configured.
- `make cloud-kill` stops worker launches and running demo compute.
- `make cloud-down` removes runtime compute while preserving foundation resources.
- `CONFIRM=DELETE_DEMO_DATA make cloud-nuke` deletes the entire demo environment.

AWS budget data and Cost Explorer are delayed. They are useful monitoring controls, not an immediate hard cost cutoff.

## Production hardening roadmap

The current deployment is intentionally a demo-grade cloud-native architecture. A production version should add:

- Custom domain, TLS listener, and HTTPS-only API ingress.
- Authentication, tenant isolation, and authorization.
- robots.txt enforcement, crawl politeness, per-domain rate limits, and abuse controls.
- Separate least-privilege task roles for API and worker responsibilities.
- Centralized secret rotation and audit policies.
- WAF, request-rate limits, and structured audit events.
- A controlled worker-concurrency mechanism for higher message volumes.
- Distributed tracing, metrics, alarms, dashboards, and SLOs.
- Search indexing, retrieval, and evidence-backed agent workflows.
