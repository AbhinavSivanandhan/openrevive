# OpenRevive Requirements

## Scope

OpenRevive is a **bounded crawler-first research workspace**. It is designed to run small, inspectable campaigns against user-approved domains, retain the resulting source evidence, and produce an explicit source-linked campaign brief.

It is not intended to crawl the public web at global scale, provide freshness guarantees, or act as a general web-archive/search/chat platform. Requirement priorities below make that distinction explicit:

- **V0:** core crawler workflow and correctness boundaries.
- **V1:** delivered research-assistance, operational, and cloud-demo features built on that workflow.
- **V2:** intentionally deferred growth toward archival, monitoring, search, and larger-scale operation.

Status labels:

```text
Implemented = present in the current repository.
Partial     = implemented with an explicit limitation.
Deferred    = not implemented; documented only as later scope.
```

## Functional requirements

| ID | Priority | Status | Requirement |
| --- | --- | --- | --- |
| FR-1 | V0 | Implemented | Create a durable workspace/collection/campaign hierarchy. The API supports workspace and collection CRUD reads/creates; the current browser UI initializes one demo workspace and collection on first use. |
| FR-2 | V0 | Implemented | Accept a bounded crawl request with seed URLs, allowed domains, research intent, maximum pages, maximum depth, request timeout, retry budget, name, and idempotency key. |
| FR-3 | V0 | Implemented | Create one durable normalized seed job per accepted seed URL. Repeated normalized URLs must not become duplicate jobs within a campaign. |
| FR-4 | V0 | Implemented | Start, pause, resume, cancel, list, and inspect campaign state without performing web crawling in the API request handler. |
| FR-5 | V0 | Implemented | Fetch bounded HTTP/HTTPS responses, record status/bytes/duration/error state, and move each job through durable lease, retry, success, failure, or cancellation state. |
| FR-6 | V0 | Implemented | Persist raw fetched artifacts to MinIO locally or S3 in AWS, then persist a durable document record containing source URL, object key, content hash/type, title, and extracted text. |
| FR-7 | V0 | Implemented | Enforce campaign-scoped URL deduplication, maximum-page capacity, allowed-domain filtering, timeout, and maximum-attempt controls. |
| FR-8 | V0 | Partial | Discover in-scope links from successful depth-zero HTML seed pages, persist selected child jobs with parent URL, anchor text, priority, and discovery reason. Discovery is not yet recursive from depth-one jobs. |
| FR-9 | V0 | Implemented | Show the campaign frontier, job statuses, failure metadata, persisted documents, and document reader in the browser UI. |
| FR-10 | V1 | Implemented | Use Nova Micro only after deterministic filtering to select a bounded set of supplied root-page candidate IDs. The model must never invent URLs. |
| FR-11 | V1 | Implemented | Generate or retrieve an explicit campaign brief only after a `SUCCEEDED` or `PARTIALLY_SUCCEEDED` campaign. The brief must be based on persisted extracted evidence, not live web search. |
| FR-12 | V1 | Implemented | Validate brief JSON and source references before storing it. Each stored finding maps to persisted document IDs shown in the UI. |
| FR-13 | V1 | Implemented | Cache one campaign brief per corpus fingerprint. Unchanged evidence/model/prompt inputs return the existing `GENERATING`, `READY`, or `FAILED` artifact; failures retry only on a later explicit request. |
| FR-14 | V1 | Implemented | Support a bounded direct or map-reduce brief path: up to four map groups plus one reducer call. |
| FR-15 | V2 | Deferred | Re-crawl sources on schedules or change signals and retain repeated snapshots over time. |
| FR-16 | V2 | Deferred | Detect link rot and material content drift, deduplicate alerts, and generate recovery/monitoring reports. |
| FR-17 | V2 | Deferred | Provide full-text search, vector retrieval, general chat, or an unrestricted research-answer API. |
| FR-18 | V2 | Deferred | Support authenticated source connectors, browser rendering, PDFs, OCR, images, and structured extraction adapters. |

## Non-functional requirements

| ID | Priority | Status | Requirement |
| --- | --- | --- | --- |
| NFR-1 | V0 | Implemented | **Durable work state.** Campaigns, jobs, evidence metadata, workers, domain policies, and brief cache state live in PostgreSQL rather than process memory or the SQS message body. |
| NFR-2 | V0 | Implemented | **Safe concurrent claims.** Multiple workers may try to claim work. PostgreSQL `FOR UPDATE SKIP LOCKED`, durable job leases, lease expiry, and token checks prevent two workers from authoritatively completing the same job. |
| NFR-3 | V0 | Implemented | **Safe recovery.** A worker that dies leaves an expiring job/domain lease. Another worker may reclaim expired work, and the original stale worker cannot subsequently finalize it. |
| NFR-4 | V0 | Implemented | **Campaign boundedness.** Page count, depth, retry, request timeout, response-size, and allowed-domain controls prevent an unbounded crawl request. |
| NFR-5 | V0 | Implemented | **Current politeness rule.** The system admits one active HTTP request per hostname across every campaign and applies a default one-second cooldown after each completed/failed request. |
| NFR-6 | V0 | Partial | **Content safety.** HTTP/HTTPS and allowed-domain checks run before discovery persistence; response size is bounded. robots.txt, `nofollow`, site-specific crawl delay, and broader abuse controls are not enforced yet. |
| NFR-7 | V0 | Implemented | **Evidence integrity.** Raw artifacts use deterministic object keys and a SHA-256 content hash; the database keeps the provenance needed to connect a document to its crawl job and source URL. |
| NFR-8 | V1 | Implemented | **Observable operation.** Durable job state, worker heartbeats, CloudWatch logs, campaign/frontier/document endpoints, and smoke/status commands expose the execution path. |
| NFR-9 | V1 | Implemented | **Bounded AI cost and provenance.** Frontier selection accepts only supplied IDs; brief evidence is relevance-ranked and character-capped; direct synthesis makes one call and map-reduce makes at most five; finding citations are validated. |
| NFR-10 | V1 | Implemented | **Demo deployment security.** Aurora is private; task roles read runtime credentials from Secrets Manager; deployed Vercel/API access uses Basic Auth; worker tasks expose no inbound port. |
| NFR-11 | V1 | Implemented | **Explicit cost controls.** The demo uses finite workers, one API service replica, Aurora capacity settings, object/image lifecycle policies, budget configuration, and explicit stop/down/nuke commands. |
| NFR-12 | V2 | Deferred | **Measured global throughput.** There is no target such as thousands of pages per second. Each worker currently handles one job lifecycle at a time, and no application-level global worker cap or high-throughput scheduler exists. |
| NFR-13 | V2 | Deferred | **Freshness guarantees.** The system does not promise that a source will be revisited within a time interval because scheduled re-crawling is not implemented. |
| NFR-14 | V2 | Deferred | **Production identity and tenancy.** Basic Auth is a demo gate, not user identity, tenant isolation, role-based authorization, or audit-grade access control. |
| NFR-15 | V2 | Deferred | **Production reliability targets.** No formal SLO, RPO/RTO, cross-region failover, WAF, tracing, metrics/alerts, or capacity-autoscaling guarantee is claimed. |

## Current operational limits and consequences

### Crawler scope is intentionally small

The browser UI creates one-root campaigns using a 50-page, depth-2, 20-second, two-attempt profile. The API accepts bounded values up to its own validation limits, but this project is not tuned or documented as a global crawler.

### Depth is a stored bound, not yet full recursion

`max_depth` is stored on `CrawlRun` and checked when child jobs are inserted. However, only successful depth-zero HTML jobs invoke link discovery. A campaign can create P1 jobs from a root; it does not currently discover P2 jobs from P1 pages.

### Parallelism is mostly across domains

One worker process handles one URL lifecycle at a time. Multiple workers can operate safely, but the domain-policy row permits only one active request to a hostname. Extra workers are useful when ready work spans distinct domains; they do not increase throughput for a single-host campaign.

### AI is constrained by evidence and interface

OpenRevive does not claim that model output is exhaustive, correct, or independently verified. Its guarantees are narrower:

```text
- models see bounded campaign evidence or approved candidate metadata;
- models cannot add frontier URLs beyond supplied candidates;
- stored brief findings must cite supplied evidence references;
- unchanged campaign inputs return the cached brief rather than silently calling the model again.
```

## Interview summary

```text
V0: run a small approved-domain crawl correctly, durably, and politely.
V1: make the crawler inspectable, cloud-operable, and evidence-grounded with bounded AI assistance.
V2: add recursive freshness, monitoring, archival history, search, stronger identity, and measured scale only when the current foundations require them.
```
