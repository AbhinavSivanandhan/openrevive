# User Flow and Use Cases

## 1. What OpenRevive is

OpenRevive is a **crawler-first research workspace**.

A user starts with one approved root source and a concrete research question. OpenRevive then creates a bounded, inspectable campaign:

```text id="1gqz5z"
Workspace
  -> Collection
      -> Campaign
          -> Durable crawl frontier
          -> Persisted documents and raw evidence
          -> Optional source-linked AI brief
```

The product is designed to make the crawl visible and reviewable.

It is not a one-click bulk scraper, a browser automation product, a general chat interface, or a web-scale search engine. The user can see which URLs entered the frontier, why they were selected, what failed, which documents were captured, and which sources support an AI-generated finding.

## 2. Product terms

| Term                | Meaning                                                                                                                    |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Workspace**       | Top-level organizational boundary for related research.                                                                    |
| **Collection**      | A group of related campaigns inside a workspace.                                                                           |
| **Campaign**        | One bounded crawl configuration and its durable execution history.                                                         |
| **Root URL**        | The starting page supplied by the user. The browser UI accepts one root URL.                                               |
| **Allowed domain**  | The hostname scope that the crawler is permitted to fetch. The browser UI derives it from the root URL.                    |
| **Research intent** | The concrete question or topic used to rank links and curate evidence.                                                     |
| **Frontier**        | Durable database-backed crawl jobs waiting to be claimed, being processed, retried, completed, or failed.                  |
| **Document**        | Persisted evidence from one successful fetch: extracted text and metadata in PostgreSQL, plus raw bytes in object storage. |
| **Campaign brief**  | An explicit Nova Micro synthesis over persisted campaign evidence with validated document references.                      |

## 3. Browser workflow

The browser UI is intentionally focused. It provides one root URL, one crawl scope, one research intent, and a fixed bounded profile.

```text id="5zll6f"
Open control plane
  -> create and start campaign
  -> redirect immediately to campaign workspace
  -> observe durable crawl state
  -> inspect documents
  -> generate source-linked campaign brief after completion
```

### 3.1 First use: local demo workspace

The first time a user creates a campaign in a browser, the UI creates:

```text id="mmn4sy"
Workspace:  OpenRevive Demo Workspace
Collection: Crawler Research
```

The browser stores the resulting collection ID in local storage:

```text id="u5o1p9"
openrevive.demo.collection-id
```

This is a demo convenience, not a user-account system.

The UI does not currently provide workspace switching, collection creation, collection selection, sharing, or membership management. Those concepts exist in the API and data model, but not as browser-facing product features.

### 3.2 Create a campaign

The control-plane form asks for:

| Field             | Browser behavior                                                                                          |
| ----------------- | --------------------------------------------------------------------------------------------------------- |
| Campaign name     | Required short label used in the campaign library and workspace.                                          |
| Campaign root URL | Required `http` or `https` URL. Its hostname becomes the allowed crawl domain.                            |
| Research intent   | Required objective that guides deterministic ranking, AI-assisted link selection, and evidence synthesis. |

The browser submits this fixed profile:

```text id="3478th"
Maximum pages:               50
Maximum depth:               2
Request timeout:             20 seconds
Maximum attempts per job:    2
```

The user does not configure these limits in the current UI.

The browser also sends a fresh `Idempotency-Key` for the creation request. This prevents a repeated browser submission from creating duplicate campaigns when a network retry occurs.

### 3.3 What happens after submission

When the user selects **Create and open campaign**, the browser:

1. validates the root URL;
2. creates the local demo workspace and collection if needed;
3. creates a durable `PENDING` campaign and root crawl job;
4. starts the campaign;
5. redirects immediately to:

```text id="37sj2a"
/campaigns/<campaign-id>
```

The redirect does not wait for crawling to finish.

The API stores the campaign in PostgreSQL before any external fetch occurs. Starting the campaign changes it to `RUNNING` and, in AWS, sends a compact wake-up event to SQS after the transaction commits.

```text id="vwfut7"
Browser
  -> API creates campaign and root job
  -> API marks campaign RUNNING
  -> SQS wake-up event
  -> EventBridge Pipe
  -> finite worker task
  -> PostgreSQL job claim
  -> bounded external fetch
```

In local Docker Compose, the continuously running worker polls PostgreSQL directly. There is no local SQS or EventBridge dependency.

## 4. What the crawler does

### 4.1 Root-page crawl

The campaign begins with one depth-zero root job.

```text id="iwb6je"
P0 root URL
  -> worker claims durable job lease
  -> worker reserves the domain
  -> bounded HTTP fetch
  -> raw response bytes written to MinIO or S3
  -> extracted document persisted in PostgreSQL
  -> root job marked SUCCEEDED
```

The crawler accepts only supported HTML or XHTML pages. It rejects redirects, unsupported content types, oversized responses, unsupported URL schemes, credentials embedded in URLs, and URLs outside the allowed domain scope.

### 4.2 Link discovery and selection

When the root page fetch succeeds and the campaign has a research intent, OpenRevive may expand the frontier.

The worker:

1. extracts links from the root HTML;
2. normalizes URLs;
3. removes duplicates, tracking parameters, likely non-HTML assets, unsupported URLs, and out-of-scope domains;
4. applies deterministic ranking using the research intent, anchor text, and URL path;
5. sends only bounded candidate metadata to Nova Micro;
6. persists a small set of selected child URLs as durable high-priority crawl jobs.

Nova Micro does not receive arbitrary permission to create URLs. It can select only IDs from the candidate set prepared by the application.

The effective current crawl shape is:

```text id="2p4e02"
P0 root page
  -> bounded AI-selected P1 child pages
```

The browser UI labels the frontier generically as:

```text id="5q4yfv"
P0 -> P1 -> P2 crawl queue
```

However, the current worker expands only successful depth-zero root pages. Selected depth-one pages are fetched and persisted, but they do not start another discovery pass. In practice, the current implementation creates `P0` and `P1` jobs, not recursive `P2` jobs.

### 4.3 Domain pacing

The crawler uses a shared PostgreSQL domain-policy record for each hostname.

Current behavior:

* only one active request may run for a hostname at a time;
* the worker applies a cooldown after a request completes or fails;
* the default cooldown is one second;
* another campaign targeting the same hostname shares that pacing state;
* a job blocked by pacing remains pending and does not consume an attempt.

This is a global domain-pacing control, not a `robots.txt` implementation.

The current system does **not** fetch, parse, or enforce `robots.txt`.

## 5. Campaign workspace

The campaign workspace is the primary product view.

It auto-refreshes durable campaign state every two seconds.

The workspace shows:

* campaign name and root URL;
* research intent;
* campaign lifecycle status;
* total, completed, active, queued, and failed job counts;
* available campaign lifecycle actions;
* the durable crawl frontier;
* fetched document cards;
* explicit campaign-brief controls and results.

### 5.1 Campaign lifecycle actions

Available actions depend on the campaign status.

| Campaign status       | Browser actions     |
| --------------------- | ------------------- |
| `PENDING`             | Start, cancel       |
| `RUNNING`             | Pause, cancel       |
| `PAUSED`              | Resume, cancel      |
| `SUCCEEDED`           | No lifecycle action |
| `PARTIALLY_SUCCEEDED` | No lifecycle action |
| `FAILED`              | No lifecycle action |
| `CANCELLED`           | No lifecycle action |

#### Pause

Pausing prevents workers from claiming new jobs.

A worker that already has a valid lease may still finish its current request and persist its results. The campaign can later resume from the durable frontier.

#### Resume

Resuming returns a paused campaign to `RUNNING` and sends another wake-up event in AWS.

A resume is safe even when an earlier wake-up event was delayed or duplicated because PostgreSQL leases remain the source of truth for job ownership.

#### Cancel

Cancelling marks the campaign as `CANCELLED` and cancels pending or retry-pending jobs.

A job already leased by a worker is not forcibly terminated. It may complete its current request, but subsequent state handling prevents future normal crawl progression.

### 5.2 Frontier inspection

The live frontier table makes crawl work inspectable.

For every durable job, it shows:

| Field            | Meaning                                                                           |
| ---------------- | --------------------------------------------------------------------------------- |
| Depth            | `P0` for the supplied root page; `P1` for a selected child page.                  |
| Priority         | Deterministic or selected priority band and numeric score.                        |
| State            | Current durable job state, such as `PENDING`, `LEASED`, `SUCCEEDED`, or `FAILED`. |
| URL              | The original requested URL.                                                       |
| Anchor text      | Link text that led to a discovered child page, when available.                    |
| Discovery reason | Persisted context for why the URL entered the frontier.                           |
| Failure          | Structured error code and message for a failed job.                               |

The frontier is not only a progress indicator. It is the audit trail for how a campaign moved from a root page to captured evidence.

### 5.3 Persisted documents

Each successful job creates a document card.

A document card includes:

* extracted title;
* source URL;
* content type;
* extracted-text preview;
* link to the persisted document reader.

The document reader shows:

* crawled URL;
* capture timestamp;
* content type;
* full extracted text;
* raw artifact key;
* link to open the original source page in a new tab.

The raw artifact key identifies the object stored in MinIO locally or S3 in AWS. The browser does not provide direct raw-artifact download or object-browser access.

### 5.4 Campaign library

The campaign library is available at:

```text id="cbrhq2"
/campaigns
```

It refreshes every three seconds and shows campaigns in the browser’s stored demo collection.

Each card includes:

* campaign status;
* campaign name or fallback label;
* root URL;
* creation time;
* total jobs;
* fetched jobs;
* active jobs;
* queued jobs.

The library is collection-scoped. A browser with no stored `openrevive.demo.collection-id` sees an empty-state message and is directed to create a campaign from the control plane.

## 6. Evidence-backed AI brief

Campaign briefs are explicit user actions.

OpenRevive does not automatically call Bedrock after every crawl. A user chooses **Generate AI brief** only after the campaign reaches one of these terminal states:

```text id="da0nmn"
SUCCEEDED
PARTIALLY_SUCCEEDED
```

The API then:

1. loads persisted extracted text from successful campaign documents;
2. ranks and bounds the evidence set;
3. deduplicates equivalent content;
4. sends source cards and research intent to Nova Micro;
5. validates the model response against the supplied source set;
6. stores the resulting brief in PostgreSQL.

The brief can contain:

* overview;
* key findings;
* source document links for each finding;
* open questions;
* recommended follow-ups;
* synthesis metadata such as direct versus map-reduce execution and model-call count.

### 6.1 Brief caching

OpenRevive caches one brief for each unchanged campaign evidence corpus.

```text id="3v8554"
same campaign evidence + same intent + same model/prompt configuration
  -> return cached brief
```

The UI shows **Open cached brief** when a valid brief already exists.

For larger evidence sets, the system can use bounded map-reduce synthesis. The current implementation caps that process at five total Nova Micro calls.

### 6.2 Brief failure and retry

A failed brief does not delete crawl evidence.

The UI exposes **Retry AI brief** only after a brief failure. Another model request occurs only when the user explicitly selects that button.

This keeps model use intentional and avoids repeated automated charges or retries.

## 7. Recommended use cases

OpenRevive works best when a user has:

* a trusted root page;
* a narrow domain scope;
* a concrete technical objective;
* a modest number of related HTML pages;
* a reason to inspect source evidence rather than accept a black-box answer.

### 7.1 Technical documentation research

This is the strongest current use case.

Examples:

```text id="rf7whq"
Python asyncio cancellation and task groups
FastAPI dependency injection and request lifecycle
Kubernetes network policies and service discovery
PostgreSQL transaction isolation and locking
AWS ECS task roles, task networking, and deployment behavior
OpenTelemetry trace context propagation
```

A focused intent gives the deterministic ranker and Nova Micro useful signals when choosing related links from the root page.

### 7.2 Open-source project reconnaissance

Start from a project documentation page, architecture page, or contributor guide.

Examples:

```text id="w25r7i"
How an open-source workflow engine schedules workers
How a database migration tool represents revisions
How a framework handles dependency injection
How a cloud SDK configures credentials and retries
```

This is useful when the user needs a bounded evidence set before contributing, reviewing architecture, preparing for an interview, or learning an unfamiliar codebase.

### 7.3 Platform and API capability mapping

Start from a platform documentation root and collect directly related pages.

Examples:

```text id="ugyuzk"
AWS Bedrock inference profile behavior
GitHub Actions reusable workflow syntax
Stripe webhook signature verification
Cloudflare Workers runtime limits
Vercel deployment and environment-variable behavior
```

The resulting campaign shows the source pages, crawl path, extracted text, and a bounded synthesis with document references.

### 7.4 Evidence-first technical briefing

Use a campaign brief when the objective is to summarize captured documentation, identify key implementation constraints, surface questions, and preserve source links.

A useful research intent looks like:

```text id="m655b1"
Explain how structured concurrency works in asyncio, identify the
cancellation rules that affect application design, and list the source pages
that justify each recommendation.
```

A weak research intent looks like:

```text id="w8d2oo"
Tell me everything about Python.
```

The system is designed for bounded relevance, not broad web search.

### 7.5 Distributed-systems demonstration

OpenRevive is also a useful technical demo because it makes operational state visible.

A walkthrough can show:

```text id="wmz4i1"
campaign created
  -> durable frontier persisted
  -> SQS wake-up event
  -> finite worker task
  -> PostgreSQL lease claim
  -> bounded HTTP fetch
  -> raw artifact in S3
  -> extracted document in Aurora
  -> source-linked campaign brief
```

The visible frontier, document reader, worker logs, S3 artifact, cloud smoke test, and Terraform split provide concrete evidence of the architecture.

## 8. Current product boundaries

OpenRevive deliberately has a narrower scope than the original broader knowledge-recovery concept.

### 8.1 Browser UI boundaries

The current browser UI:

* accepts one root URL;
* derives one allowed hostname from that URL;
* uses fixed crawl limits;
* stores one demo collection ID in browser local storage;
* has no login, user identity, workspace switcher, or shared-team workflow;
* polls for updates rather than using real-time push events.

The API supports multiple seed URLs and multiple allowed domains, but the browser UI does not expose those advanced configuration options.

### 8.2 Crawl boundaries

The current crawler:

* supports HTTP and HTTPS only;
* fetches HTML and XHTML only;
* does not render browser JavaScript;
* does not crawl PDFs, images, video, audio, feeds, archives, or authenticated pages;
* rejects redirects instead of following them;
* enforces a page budget and retry budget;
* applies global hostname pacing;
* does not enforce `robots.txt`;
* expands only successful depth-zero root pages;
* does not recursively discover from depth-one pages.

### 8.3 Research and AI boundaries

The current product:

* has no general chat interface;
* has no vector database, embedding pipeline, or semantic search interface;
* has no full-text search UI;
* has no autonomous agent that explores arbitrary websites;
* does not create briefs automatically;
* limits AI frontier selection to supplied candidate IDs;
* limits campaign briefs to persisted campaign evidence;
* validates source references before storing a brief.

### 8.4 Knowledge-management boundaries

OpenRevive does not currently provide:

* recurring crawls;
* scheduled monitoring;
* page snapshot histories;
* content-drift detection;
* link-rot alerts;
* side-by-side page diffs;
* recovery recommendations;
* retention controls exposed in the browser;
* long-term archival or backup workflows.

Those are potential future directions, not current product claims.

## 9. Choosing a good campaign

Use this checklist before creating a campaign.

| Good campaign input              | Why it works                                                         |
| -------------------------------- | -------------------------------------------------------------------- |
| Stable public documentation root | The worker can fetch it without authentication or browser rendering. |
| One narrow technical question    | Gives link ranking and synthesis a concrete target.                  |
| Domain-owned documentation site  | Keeps scope and related links predictable.                           |
| HTML-heavy content               | Matches the current extractor and fetcher.                           |
| Modest expected source set       | Fits the bounded frontier and evidence plan.                         |

Avoid using the current product for:

| Poor fit                         | Why                                                                       |
| -------------------------------- | ------------------------------------------------------------------------- |
| Large general-web research       | The crawler is intentionally bounded and not recursive beyond one hop.    |
| JavaScript-only websites         | The worker does not use a browser renderer.                               |
| Login-protected documentation    | The worker does not support authenticated sessions or connectors.         |
| PDF-heavy research               | PDF extraction is not implemented.                                        |
| Compliance archival              | There is no immutable retention, version history, or legal-hold workflow. |
| Continuous website monitoring    | There are no schedules, drift checks, or alerts.                          |
| Multi-user research repositories | There is no user or tenant model.                                         |

## 10. Example end-to-end campaign

A representative campaign:

```text id="pk9kah"
Campaign name:
Asyncio Structured Concurrency

Root URL:
https://docs.python.org/3/library/asyncio-task.html

Derived allowed domain:
docs.python.org

Research intent:
Explain task groups, cancellation propagation, exception groups, and the
practical differences between gather() and TaskGroup for production services.
```

Expected workflow:

```text id="4um8gq"
1. Browser creates the campaign and root P0 job.
2. Browser starts the campaign and opens its workspace.
3. Worker fetches the root asyncio task documentation page.
4. Worker stores raw HTML and extracted text.
5. Worker evaluates in-scope links from the root page.
6. Nova Micro selects a bounded set of relevant P1 links from supplied IDs.
7. Worker fetches selected P1 pages while following domain pacing.
8. Browser shows completed jobs and captured documents.
9. User reads the most relevant documents.
10. User explicitly generates a source-linked campaign brief.
```

The expected output is not “all information about asyncio.” It is a bounded, inspectable source set and a briefing that points back to the documents captured by this specific campaign.
