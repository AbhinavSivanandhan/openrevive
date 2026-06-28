"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

const STORAGE_KEYS = {
  collectionId: "openrevive.demo.collection-id",
  crawlRunId: "openrevive.demo.crawl-run-id",
};

const TERMINAL_STATUSES = new Set([
  "SUCCEEDED",
  "FAILED",
  "PARTIALLY_SUCCEEDED",
]);

type Workspace = {
  id: string;
};

type Collection = {
  id: string;
};

type CrawlRunCreateResponse = {
  id: string;
  collection_id: string;
  status: string;
};

type CrawlRunDetail = {
  id: string;
  collection_id: string;
  status: string;
  job_counts: Record<string, number>;
  created_at: string;
};

type CrawlRunSummary = {
  id: string;
  collection_id: string;
  status: string;
  seed_urls: string[];
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  job_counts: Record<string, number>;
};

type CrawlRunList = {
  total: number;
  items: CrawlRunSummary[];
};

type CrawledDocument = {
  id: string;
  crawl_job_id: string;
  source_url: string;
  title: string | null;
  extracted_text_preview: string | null;
  raw_object_key: string;
  content_type: string;
  created_at: string;
};

type CrawledDocumentList = {
  total: number;
  items: CrawledDocument[];
};

type CampaignAction = "start" | "pause" | "resume" | "cancel";

function parseUrls(value: string): string[] {
  return value
    .split("\n")
    .map((url) => url.trim())
    .filter(Boolean);
}

function allowedDomains(urls: string[]): string[] {
  return Array.from(
    new Set(
      urls.map((url) => {
        const parsed = new URL(url);
        return parsed.hostname.toLowerCase();
      }),
    ),
  );
}

async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);

  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    const errorBody: unknown = await response.json().catch(() => null);
    let detail = "";

    if (
      typeof errorBody === "object" &&
      errorBody !== null &&
      "detail" in errorBody
    ) {
      const value = (errorBody as { detail?: unknown }).detail;
      detail =
        typeof value === "string"
          ? value
          : JSON.stringify(value);
    }

    throw new Error(
      detail || `Request failed with HTTP ${response.status}.`,
    );
  }

  return response.json() as Promise<T>;
}

function statusClass(status: string): string {
  if (status === "FAILED" || status === "PARTIALLY_SUCCEEDED") {
    return "run-status failed";
  }

  if (status === "PENDING" || status === "RUNNING") {
    return "run-status pending";
  }

  return "run-status";
}

export default function Home() {
  const [seedUrls, setSeedUrls] = useState(
    [
      "https://docs.python.org/3/library/asyncio.html",
      "https://docs.python.org/3/library/dataclasses.html",
    ].join("\n"),
  );
  const [collectionId, setCollectionId] = useState<string | null>(null);
  const [crawlRun, setCrawlRun] = useState<CrawlRunDetail | null>(null);
  const [campaigns, setCampaigns] = useState<CrawlRunSummary[]>([]);
  const [campaignsLoading, setCampaignsLoading] = useState(false);
  const [documents, setDocuments] = useState<CrawledDocument[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [campaignAction, setCampaignAction] =
    useState<CampaignAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isTerminal = useMemo(
    () => crawlRun !== null && TERMINAL_STATUSES.has(crawlRun.status),
    [crawlRun],
  );

  const refreshCampaigns = useCallback(
    async (nextCollectionId: string) => {
      setCampaignsLoading(true);

      try {
        const history = await apiRequest<CrawlRunList>(
          `/v1/collections/${nextCollectionId}/crawl-runs`,
        );

        setCampaigns(history.items);
      } catch (historyError) {
        setError(
          historyError instanceof Error
            ? historyError.message
            : "Unable to load campaign history.",
        );
      } finally {
        setCampaignsLoading(false);
      }
    },
    [],
  );

  const refreshRun = useCallback(
    async (nextCollectionId: string, nextRunId: string) => {
      setRefreshing(true);

      try {
        const [detail, documentList] = await Promise.all([
          apiRequest<CrawlRunDetail>(
            `/v1/collections/${nextCollectionId}/crawl-runs/${nextRunId}`,
          ),
          apiRequest<CrawledDocumentList>(
            `/v1/collections/${nextCollectionId}/crawl-runs/${nextRunId}/documents`,
          ),
        ]);

        setCrawlRun(detail);
        setDocuments(documentList.items);
      } catch (refreshError) {
        setError(
          refreshError instanceof Error
            ? refreshError.message
            : "Unable to refresh crawl status.",
        );
      } finally {
        setRefreshing(false);
      }
    },
    [],
  );

  const ensureDemoCollection = useCallback(async (): Promise<string> => {
    const storedCollectionId = window.localStorage.getItem(
      STORAGE_KEYS.collectionId,
    );

    if (storedCollectionId) {
      return storedCollectionId;
    }

    const workspace = await apiRequest<Workspace>("/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({
        name: "OpenRevive Demo Workspace",
      }),
    });

    const collection = await apiRequest<Collection>(
      `/v1/workspaces/${workspace.id}/collections`,
      {
        method: "POST",
        body: JSON.stringify({
          name: "Crawler Research",
          description:
            "Crawled sources and evidence collected by OpenRevive.",
        }),
      },
    );

    window.localStorage.setItem(
      STORAGE_KEYS.collectionId,
      collection.id,
    );

    return collection.id;
  }, []);

  useEffect(() => {
    const savedCollectionId = window.localStorage.getItem(
      STORAGE_KEYS.collectionId,
    );
    const savedCrawlRunId = window.localStorage.getItem(
      STORAGE_KEYS.crawlRunId,
    );

    if (!savedCollectionId) {
      return;
    }

    setCollectionId(savedCollectionId);
    void refreshCampaigns(savedCollectionId);

    if (savedCrawlRunId) {
      void refreshRun(savedCollectionId, savedCrawlRunId);
    }
  }, [refreshCampaigns, refreshRun]);

  useEffect(() => {
    if (
      collectionId === null ||
      crawlRun === null ||
      TERMINAL_STATUSES.has(crawlRun.status)
    ) {
      return;
    }

    const timer = window.setInterval(() => {
      void refreshRun(collectionId, crawlRun.id);
    }, 2000);

    return () => window.clearInterval(timer);
  }, [collectionId, crawlRun, refreshRun]);

  async function startCrawl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    try {
      const urls = parseUrls(seedUrls);

      if (urls.length !== 1) {
        throw new Error(
          "Create one campaign per root URL. Add exactly one URL.",
        );
      }

      const rootUrl = urls[0];
      const domains = allowedDomains([rootUrl]);
      setSubmitting(true);

      const nextCollectionId =
        collectionId ?? (await ensureDemoCollection());

      setCollectionId(nextCollectionId);

      const response = await apiRequest<CrawlRunCreateResponse>(
        `/v1/collections/${nextCollectionId}/crawl-runs`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({
            seed_urls: [rootUrl],
            allowed_domains: domains,
            max_pages: 100,
            max_depth: 3,
            request_timeout_seconds: 20,
            max_attempts: 2,
          }),
        },
      );

      window.localStorage.setItem(
        STORAGE_KEYS.crawlRunId,
        response.id,
      );

      const startedCampaign = await apiRequest<CrawlRunDetail>(
        `/v1/collections/${nextCollectionId}/crawl-runs/${response.id}/start`,
        {
          method: "POST",
        },
      );

      setCrawlRun(startedCampaign);
      setDocuments([]);

      await refreshCampaigns(nextCollectionId);
      await refreshRun(nextCollectionId, response.id);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to start the crawl.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function controlCampaign(action: CampaignAction) {
    if (collectionId === null || crawlRun === null) {
      return;
    }

    if (
      action === "cancel" &&
      !window.confirm(
        "Cancel this campaign? Queued URLs will stop permanently.",
      )
    ) {
      return;
    }

    setError(null);
    setCampaignAction(action);

    try {
      const detail = await apiRequest<CrawlRunDetail>(
        `/v1/collections/${collectionId}/crawl-runs/${crawlRun.id}/${action}`,
        {
          method: "POST",
        },
      );

      setCrawlRun(detail);
      await refreshCampaigns(collectionId);
      await refreshRun(collectionId, crawlRun.id);
    } catch (actionError) {
      setError(
        actionError instanceof Error
          ? actionError.message
          : "Unable to update campaign state.",
      );
    } finally {
      setCampaignAction(null);
    }
  }

  async function selectCampaign(campaignId: string) {
    if (collectionId === null) {
      return;
    }

    setError(null);
    setDocuments([]);

    window.localStorage.setItem(
      STORAGE_KEYS.crawlRunId,
      campaignId,
    );

    await refreshRun(collectionId, campaignId);
  }

  const totalJobs = crawlRun?.job_counts.TOTAL ?? 0;
  const succeededJobs = crawlRun?.job_counts.SUCCEEDED ?? 0;
  const activeLeases = crawlRun?.job_counts.LEASED ?? 0;
  const queuedJobs =
    (crawlRun?.job_counts.PENDING ?? 0) +
    (crawlRun?.job_counts.RETRY_PENDING ?? 0);
  const failedJobs = crawlRun?.job_counts.FAILED ?? 0;

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">O</div>
          <div className="brand-copy">
            <strong>OpenRevive</strong>
            <span>Crawler-first research workspace</span>
          </div>
        </div>

        <div className="status-chip">
          <span className="status-dot" />
          {refreshing ? "Refreshing crawl state" : "Control plane online"}
        </div>
      </header>

      <section className="campaign-history-panel">
        <div className="campaign-history-heading">
          <div>
            <p className="eyebrow">Campaign control plane</p>
            <h2>Research campaigns</h2>
          </div>
          <span className="campaign-history-count">
            {campaignsLoading
              ? "Loading…"
              : `${campaigns.length} stored`}
          </span>
        </div>

        {campaigns.length === 0 && !campaignsLoading ? (
          <div className="campaign-history-empty">
            Create a root campaign to begin collecting research sources.
          </div>
        ) : (
          <div className="campaign-history-list">
            {campaigns.map((campaign) => {
              const isSelected = crawlRun?.id === campaign.id;
              const rootUrl =
                campaign.seed_urls[0] ?? "Untitled campaign";

              return (
                <button
                  aria-pressed={isSelected}
                  className={
                    isSelected
                      ? "campaign-history-card selected"
                      : "campaign-history-card"
                  }
                  key={campaign.id}
                  onClick={() => void selectCampaign(campaign.id)}
                  title={rootUrl}
                  type="button"
                >
                  <div className="campaign-history-card-top">
                    <strong>{rootUrl}</strong>
                    <span className={statusClass(campaign.status)}>
                      {campaign.status}
                    </span>
                  </div>

                  <div className="campaign-history-metrics">
                    <span>
                      {campaign.job_counts.SUCCEEDED ?? 0} fetched
                    </span>
                    <span>
                      {campaign.job_counts.LEASED ?? 0} active
                    </span>
                    <span>
                      {campaign.job_counts.PENDING ?? 0} queued
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </section>

      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Evidence collection, not tab hoarding</p>
          <h1>Turn public pages into a durable research workspace.</h1>
          <p>
            Submit source URLs, let independent workers crawl them, and
            inspect the persisted documents, extracted text, and crawl
            status from one operational view.
          </p>
        </div>

        <form className="crawl-form" onSubmit={startCrawl}>
          <h2 className="form-heading">Create a campaign</h2>
          <p className="form-help">
            One root URL per campaign. The crawler will later expand this
            root into a controlled, priority-ranked frontier.
          </p>

          <label className="form-label">
            Campaign root URL
            <textarea
              className="seed-input"
              value={seedUrls}
              onChange={(event) => setSeedUrls(event.target.value)}
              spellCheck={false}
            />
          </label>

          <button
            className="primary-button"
            disabled={submitting}
            type="submit"
          >
            {submitting
              ? "Creating campaign…"
              : "Create & start campaign"}
          </button>
        </form>
      </section>

      {error ? <div className="error-banner">{error}</div> : null}

      {crawlRun ? (
        <section className="run-panel">
          <div className="run-summary">
            <div>
              <p className="eyebrow">Current crawl run</p>
              <h2>Operational status</h2>
              <p className="run-id">{crawlRun.id}</p>
            </div>

            <div className="campaign-status-actions">
              <span className={statusClass(crawlRun.status)}>
                {crawlRun.status}
              </span>

              <div className="campaign-controls">
                {crawlRun.status === "PENDING" ? (
                  <button
                    className="control-button"
                    disabled={campaignAction !== null}
                    onClick={() => void controlCampaign("start")}
                    type="button"
                  >
                    {campaignAction === "start" ? "Starting…" : "Start"}
                  </button>
                ) : null}

                {crawlRun.status === "RUNNING" ? (
                  <button
                    className="control-button"
                    disabled={campaignAction !== null}
                    onClick={() => void controlCampaign("pause")}
                    type="button"
                  >
                    {campaignAction === "pause" ? "Pausing…" : "Pause"}
                  </button>
                ) : null}

                {crawlRun.status === "PAUSED" ? (
                  <button
                    className="control-button"
                    disabled={campaignAction !== null}
                    onClick={() => void controlCampaign("resume")}
                    type="button"
                  >
                    {campaignAction === "resume" ? "Resuming…" : "Resume"}
                  </button>
                ) : null}

                {["PENDING", "RUNNING", "PAUSED"].includes(
                  crawlRun.status,
                ) ? (
                  <button
                    className="control-button danger-button"
                    disabled={campaignAction !== null}
                    onClick={() => void controlCampaign("cancel")}
                    type="button"
                  >
                    {campaignAction === "cancel" ? "Cancelling…" : "Cancel"}
                  </button>
                ) : null}
              </div>
            </div>
          </div>

          <div className="metrics-grid">
            <article className="metric-card">
              <span>Total jobs</span>
              <strong>{totalJobs}</strong>
            </article>
            <article className="metric-card">
              <span>Succeeded</span>
              <strong>{succeededJobs}</strong>
            </article>
            <article className="metric-card">
              <span>Active leases</span>
              <strong>{activeLeases}</strong>
            </article>
            <article className="metric-card">
              <span>Queued</span>
              <strong>{queuedJobs}</strong>
            </article>
            <article className="metric-card">
              <span>Failed</span>
              <strong>{failedJobs}</strong>
            </article>
          </div>

          <section className="documents-panel">
            <div className="documents-heading">
              <div>
                <p className="eyebrow">Persisted research sources</p>
                <h2>Fetched documents</h2>
              </div>
              <p>
                {documents.length} document
                {documents.length === 1 ? "" : "s"} available
                {isTerminal ? "" : " so far"}
              </p>
            </div>

            {documents.length === 0 ? (
              <div className="empty-state">
                <p>
                  {isTerminal
                    ? "No documents were persisted for this crawl run."
                    : "Workers are fetching pages. Documents will appear here as persistence completes."}
                </p>
              </div>
            ) : (
              <div className="document-list">
                {documents.map((item) => (
                  <article className="document-card" key={item.id}>
                    <div className="document-card-top">
                      <div>
                        <h3>{item.title ?? "Untitled crawled document"}</h3>
                        <a
                          href={item.source_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {item.source_url}
                        </a>
                      </div>
                      <span className="status-chip">
                        {item.content_type}
                      </span>
                    </div>

                    <p className="document-preview">
                      {item.extracted_text_preview ??
                        "No extracted text was available for this document."}
                    </p>

                    <code className="object-key">
                      {item.raw_object_key}
                    </code>
                  </article>
                ))}
              </div>
            )}
          </section>
        </section>
      ) : null}
    </main>
  );
}
