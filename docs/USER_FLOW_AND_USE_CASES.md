# User Flow and Use Cases

## Product flow

OpenRevive is a research-campaign workspace rather than a one-off URL scraper.

```text
Workspace
  -> Collection
      -> Campaign
          -> Durable crawl frontier
          -> Persisted documents and raw evidence
```

A campaign is bounded by:

- seed URLs;
- allowed domains;
- maximum page count;
- maximum crawl depth;
- request timeout;
- retry budget;
- research intent.

The system persists campaign state and evidence so the user can inspect what happened rather than treat crawling as an opaque background action.

## Standard user workflow

### 1. Create a workspace

A workspace is the top-level research boundary.

Example:

```text
Workspace: Python Runtime Research
```

### 2. Create a collection

A collection groups related research campaigns.

Example:

```text
Collection: Concurrency and Thread Safety
```

### 3. Create a campaign

The user supplies a bounded crawl configuration.

Example:

```text
Seed URL:
https://docs.python.org/3/library/threadsafety.html

Allowed domain:
docs.python.org

Research intent:
Understand thread safety, locks, atomicity, race conditions,
and free-threaded Python behavior.

Maximum pages:
50

Maximum depth:
2
```

Creating the campaign persists a `PENDING` crawl run and its initial durable job set. It does not synchronously crawl the seed URL.

### 4. Start the campaign

Starting a campaign transitions it to `RUNNING`.

The API commits the state transition first, then publishes an SQS wake-up event. EventBridge Pipes launches a finite Fargate worker task.

```text
RUNNING campaign
  -> SQS wake-up
  -> Fargate worker
  -> PostgreSQL job lease
  -> page fetch and discovery
  -> S3 raw artifact
  -> PostgreSQL document and job state
```

### 5. Observe the live frontier

The campaign page refreshes durable state from the API.

The user can inspect:

- campaign status;
- total, completed, active, queued, and failed jobs;
- frontier depth and priority;
- discovered URLs and discovery context;
- retry and failure state;
- persisted document count.

### 6. Read captured evidence

Successful fetches create persisted documents.

Each document includes:

- source URL;
- title;
- content type;
- extracted-text preview;
- raw artifact key;
- creation time.

The raw response is retained in S3 for the configured artifact-retention period.

## Example use cases

### Technical documentation mapping

Start from a framework, language, API, or platform documentation page.

Examples:

```text
Python concurrency and thread safety
FastAPI dependency injection
Kubernetes networking
PostgreSQL transaction isolation
AWS ECS task execution
```

Use a narrow allowed domain and modest page/depth limits to build a bounded evidence collection.

### Architecture reconnaissance

Start from a product or platform documentation root and capture a small, navigable set of related pages.

Examples:

```text
Cloud provider service documentation
Open-source project documentation
Internal engineering handbook mirror
Developer platform API reference
```

The campaign frontier makes the crawl path visible instead of hiding it behind a bulk crawl.

### Change-impact research

Run separate campaigns for adjacent technical areas, then compare the captured evidence manually.

Examples:

```text
Python free-threading documentation
Python asyncio documentation
Python threading documentation
```

The collection becomes a durable record of what sources were crawled and when.

### Controlled demo environment

The project is also designed as a distributed-systems demonstration:

```text
control plane -> durable database state -> queue wake-up
-> ephemeral worker -> object storage -> durable evidence
```

The user can show live campaign progress, worker lifecycle logs, S3 artifacts, and cloud verification commands in a technical demo.

## Current product boundaries

OpenRevive currently focuses on bounded HTTP crawling and evidence capture.

It does not yet provide:

- browser-based JavaScript rendering;
- robots.txt enforcement;
- per-domain politeness limits;
- authentication or multi-tenant authorization;
- full-text or vector search;
- answer generation or agent retrieval workflows;
- user-configurable recurring schedules;
- production-grade crawler abuse protections.

These boundaries are intentional. The current product demonstrates a durable, inspectable crawl execution model before adding retrieval or agent layers.
