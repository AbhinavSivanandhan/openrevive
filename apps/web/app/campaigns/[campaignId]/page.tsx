"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useOpenReviveCollectionId } from "../../lib/openrevive-storage";
import styles from "../campaigns.module.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

type CrawlRunDetail = {
  id: string;
  collection_id: string;
  status: string;
  name: string | null;
  research_intent: string | null;
  job_counts: Record<string, number>;
  created_at: string;
};

type CrawlFrontierJob = {
  id: string;
  parent_job_id: string | null;
  parent_url: string | null;
  original_url: string;
  normalized_url: string;
  domain: string;
  depth: number;
  anchor_text: string | null;
  priority_score: number;
  priority_band: string;
  discovery_reason: string | null;
  status: string;
  attempt_count: number;
  max_attempts: number;
  last_claimed_by_worker_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  http_status_code: number | null;
  fetched_bytes: number | null;
  fetch_duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type CrawlFrontierList = {
  total: number;
  items: CrawlFrontierJob[];
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

type CampaignBriefFinding = {
  statement: string;
  source_document_ids: string[];
};

type CampaignBriefSynthesis = {
  mode: "direct" | "map_reduce";
  model_call_count: number;
};

type CampaignBriefEvidenceGroup = {
  group_index: number;
  input_document_count: number;
  input_character_count: number;
  source_document_ids: string[];
  brief: CampaignBriefPayload;
};

type CampaignBriefPayload = {
  overview: string;
  key_findings: CampaignBriefFinding[];
  open_questions: string[];
  recommended_follow_ups: string[];
  synthesis?: CampaignBriefSynthesis;
  evidence_groups?: CampaignBriefEvidenceGroup[];
};

type CampaignBriefResponse = {
  id: string;
  crawl_run_id: string;
  status: "GENERATING" | "READY" | "FAILED";
  model_id: string;
  prompt_version: string;
  input_document_count: number;
  input_character_count: number;
  output_token_count: number | null;
  brief_json: CampaignBriefPayload | null;
  error_code: string | null;
  completed_at: string | null;
  created_at: string;
};

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
    const body: unknown = await response.json().catch(() => null);

    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      throw new Error(body.detail);
    }

    throw new Error(`Request failed with HTTP ${response.status}.`);
  }

  return response.json() as Promise<T>;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function depthLabel(depth: number): string {
  return `P${depth}`;
}

function availableActions(status: string): CampaignAction[] {
  if (status === "PENDING") {
    return ["start", "cancel"];
  }

  if (status === "RUNNING") {
    return ["pause", "cancel"];
  }

  if (status === "PAUSED") {
    return ["resume", "cancel"];
  }

  return [];
}

export default function CampaignWorkspacePage() {
  const params = useParams<{ campaignId: string }>();
  const campaignId = Array.isArray(params.campaignId)
    ? params.campaignId[0]
    : params.campaignId;

  const collectionId = useOpenReviveCollectionId();
  const [campaign, setCampaign] = useState<CrawlRunDetail | null>(null);
  const [frontier, setFrontier] = useState<CrawlFrontierJob[]>([]);
  const [documents, setDocuments] = useState<CrawledDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionInFlight, setActionInFlight] =
    useState<CampaignAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] =
    useState<CampaignBriefResponse | null>(null);
  const [briefInFlight, setBriefInFlight] = useState(false);

  const refresh = useCallback(
    async (silent = false) => {
      if (!collectionId || !campaignId) {
        return;
      }

      if (!silent) {
        setRefreshing(true);
      }

      try {
        const basePath =
          `/v1/collections/${collectionId}/crawl-runs/${campaignId}`;

        const [detail, frontierResponse, documentsResponse] =
          await Promise.all([
            apiRequest<CrawlRunDetail>(basePath),
            apiRequest<CrawlFrontierList>(`${basePath}/frontier`),
            apiRequest<CrawledDocumentList>(`${basePath}/documents`),
          ]);

        setCampaign(detail);
        setFrontier(frontierResponse.items);
        setDocuments(documentsResponse.items);
        setError(null);
      } catch (refreshError) {
        setError(
          refreshError instanceof Error
            ? refreshError.message
            : "Unable to refresh campaign workspace.",
        );
      } finally {
        setLoading(false);
        if (!silent) {
          setRefreshing(false);
        }
      }
    },
    [campaignId, collectionId],
  );

  useEffect(() => {
    if (!collectionId || !campaignId) {
      return;
    }

    const initialRefresh = window.setTimeout(() => {
      void refresh();
    }, 0);

    const timer = window.setInterval(() => {
      void refresh(true);
    }, 2000);

    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(timer);
    };
  }, [campaignId, collectionId, refresh]);

  const rootJob = useMemo(
    () => frontier.find((job) => job.depth === 0) ?? null,
    [frontier],
  );

  const counts = campaign?.job_counts ?? {};
  const queued =
    (counts.PENDING ?? 0) + (counts.RETRY_PENDING ?? 0);
  const active = counts.LEASED ?? 0;
  const completed =
    (counts.SUCCEEDED ?? 0) +
    (counts.FAILED ?? 0) +
    (counts.SKIPPED ?? 0) +
    (counts.CANCELLED ?? 0);

  async function controlCampaign(action: CampaignAction) {
    if (!collectionId || !campaignId) {
      return;
    }

    setActionInFlight(action);

    try {
      await apiRequest<CrawlRunDetail>(
        `/v1/collections/${collectionId}/crawl-runs/` +
          `${campaignId}/${action}`,
        { method: "POST" },
      );
      await refresh();
    } catch (actionError) {
      setError(
        actionError instanceof Error
          ? actionError.message
          : "Unable to update campaign.",
      );
    } finally {
      setActionInFlight(null);
    }
  }


  async function generateCampaignBrief() {
    if (!collectionId || !campaignId) {
      return;
    }

    setBriefInFlight(true);

    try {
      const response = await apiRequest<CampaignBriefResponse>(
        `/v1/collections/${collectionId}/crawl-runs/` +
          `${campaignId}/brief`,
        { method: "POST" },
      );

      setBrief(response);

      if (response.status === "FAILED") {
        setError(
          "The AI brief could not be generated. "
            + "Use the button to make one manual retry.",
        );
      } else {
        setError(null);
      }
    } catch (briefError) {
      setError(
        briefError instanceof Error
          ? briefError.message
          : "Unable to generate the campaign brief.",
      );
    } finally {
      setBriefInFlight(false);
    }
  }

  if (collectionId === null) {
    return (
      <main className={styles.page}>
        <section className={styles.emptyState}>
          <h1>Campaign workspace unavailable</h1>
          <p>
            No local OpenRevive collection was found in this browser.
          </p>
          <Link href="/" className={styles.primaryLink}>
            Go to control plane
          </Link>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>OpenRevive / campaign workspace</p>
          <h1>Live research campaign</h1>
          <p className={styles.subtle}>
            Auto-refreshing every two seconds.
          </p>
        </div>

        <div className={styles.headerActions}>
          <Link href="/" className={styles.secondaryLink}>
            Control plane
          </Link>
          <Link href="/campaigns" className={styles.secondaryLink}>
            Campaign library
          </Link>
          <button
            className={styles.secondaryButton}
            disabled={refreshing}
            onClick={() => void refresh()}
            type="button"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {error ? <p className={styles.error}>{error}</p> : null}

      {loading ? (
        <section className={styles.emptyState}>
          <h2>Loading campaign workspace…</h2>
        </section>
      ) : null}

      {campaign ? (
        <>
          <section className={styles.overview}>
            <div className={styles.overviewCopy}>
              <p className={styles.eyebrow}>Campaign</p>
              <h2>
                {campaign.name ??
                  rootJob?.original_url ??
                  campaign.id}
              </h2>
              {campaign.name && rootJob ? (
                <p className={styles.subtle}>
                  {rootJob.original_url}
                </p>
              ) : null}
              <p className={styles.intent}>
                {campaign.research_intent ??
                  "No research intent was supplied."}
              </p>
              <p className={styles.subtle}>
                Created {formatDate(campaign.created_at)}
              </p>
            </div>

            <div className={styles.statusBlock}>
              <span className={styles.status}>{campaign.status}</span>
              <div className={styles.actionRow}>
                {availableActions(campaign.status).map((action) => (
                  <button
                    className={styles.secondaryButton}
                    disabled={actionInFlight !== null}
                    key={action}
                    onClick={() => void controlCampaign(action)}
                    type="button"
                  >
                    {actionInFlight === action
                      ? `${action}…`
                      : action}
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className={styles.metrics}>
            <article>
              <span>Total jobs</span>
              <strong>{counts.TOTAL ?? 0}</strong>
            </article>
            <article>
              <span>Completed</span>
              <strong>{completed}</strong>
            </article>
            <article>
              <span>Active</span>
              <strong>{active}</strong>
            </article>
            <article>
              <span>Queued</span>
              <strong>{queued}</strong>
            </article>
            <article>
              <span>Failed</span>
              <strong>{counts.FAILED ?? 0}</strong>
            </article>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeading}>
              <div>
                <p className={styles.eyebrow}>Live frontier</p>
                <h2>P0 → P1 → P2 crawl queue</h2>
              </div>
              <span className={styles.subtle}>
                {frontier.length} durable jobs
              </span>
            </div>

            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Depth</th>
                    <th>Priority</th>
                    <th>State</th>
                    <th>URL / discovery context</th>
                    <th>Failure</th>
                  </tr>
                </thead>
                <tbody>
                  {frontier.map((job) => (
                    <tr key={job.id}>
                      <td>{depthLabel(job.depth)}</td>
                      <td>
                        <span className={styles.priority}>
                          {job.priority_band} · {job.priority_score}
                        </span>
                      </td>
                      <td>{job.status}</td>
                      <td>
                        <a
                          href={job.original_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {job.original_url}
                        </a>
                        {job.anchor_text ? (
                          <p className={styles.tableDetail}>
                            Anchor: {job.anchor_text}
                          </p>
                        ) : null}
                        {job.discovery_reason ? (
                          <p className={styles.tableDetail}>
                            {job.discovery_reason}
                          </p>
                        ) : null}
                      </td>
                      <td>
                        {job.last_error_code ? (
                          <>
                            <strong>{job.last_error_code}</strong>
                            <p className={styles.tableDetail}>
                              {job.last_error_message}
                            </p>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeading}>
              <div>
                <p className={styles.eyebrow}>Persisted evidence</p>
                <h2>Fetched documents</h2>
              </div>
              <span className={styles.subtle}>
                {documents.length} available
              </span>
            </div>

            <div className={styles.documentGrid}>
              {documents.map((document) => (
                <article className={styles.documentCard} key={document.id}>
                  <a
                    href={
                      `/campaigns/${campaignId}/documents/${document.id}`
                    }
                  >
                    {document.title ?? document.source_url}
                  </a>
                  <p className={styles.subtle}>
                    {document.content_type}
                  </p>
                  <p>
                    {document.extracted_text_preview ??
                      "No extracted preview available."}
                  </p>
                  <a
                    className={styles.readerBackLink}
                    href={
                      `/campaigns/${campaignId}/documents/${document.id}`
                    }
                  >
                    Read full document →
                  </a>
                </article>
              ))}
            </div>
          </section>
        </>
      ) : null}
      <section className={styles.section}>
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>
              Evidence-backed AI brief
            </p>
            <h2>Campaign synthesis</h2>
            <p className={styles.subtle}>
              One explicit, cached Nova Micro synthesis per unchanged
              campaign corpus. Larger evidence sets use bounded map-reduce
              synthesis with at most five total model calls.
            </p>
          </div>

          <button
            className={styles.secondaryButton}
            disabled={
              briefInFlight ||
              !["SUCCEEDED", "PARTIALLY_SUCCEEDED"].includes(
                campaign?.status ?? "",
              )
            }
            onClick={() => void generateCampaignBrief()}
            type="button"
          >
            {briefInFlight
              ? "Generating…"
              : brief?.status === "READY"
                ? "Open cached brief"
                : brief?.status === "FAILED"
                  ? "Retry AI brief"
                  : "Generate AI brief"}
          </button>
        </div>

        {!["SUCCEEDED", "PARTIALLY_SUCCEEDED"].includes(
          campaign?.status ?? "",
        ) ? (
          <p className={styles.subtle}>
            AI synthesis becomes available after this campaign reaches a
            terminal state.
          </p>
        ) : null}

        {brief?.status === "FAILED" ? (
          <p className={styles.error}>
            Brief generation failed
            {brief.error_code ? ` (${brief.error_code})` : ""}. A retry is
            only attempted when you press the button again.
          </p>
        ) : null}

        {brief?.status === "READY" && brief.brief_json ? (
          <>
            <p>
              <strong>Overview:</strong> {brief.brief_json.overview}
            </p>

            <p className={styles.subtle}>
              {brief.input_document_count} persisted source
              {brief.input_document_count === 1 ? "" : "s"} ·{" "}
              {brief.output_token_count ?? "Unknown"} generated tokens
              {brief.brief_json.synthesis ? (
                <>
                  {" "}·{" "}
                  {brief.brief_json.synthesis.mode === "map_reduce"
                    ? "Map-reduce synthesis"
                    : "Direct synthesis"}{" "}
                  · {brief.brief_json.synthesis.model_call_count} Nova
                  Micro call
                  {brief.brief_json.synthesis.model_call_count === 1
                    ? ""
                    : "s"}
                </>
              ) : null}
              {" "}·{" "}
              {brief.completed_at
                ? `generated ${formatDate(brief.completed_at)}`
                : "cached result"}
            </p>

            <div>
              <h3>Key findings</h3>
              <ul>
                {brief.brief_json.key_findings.map(
                  (finding, index) => (
                    <li key={`${finding.statement}-${index}`}>
                      <p>{finding.statement}</p>
                      <p className={styles.subtle}>
                        Sources:{" "}
                        {finding.source_document_ids.map(
                          (documentId, sourceIndex) => {
                            const document = documents.find(
                              (candidate) =>
                                candidate.id === documentId,
                            );

                            return (
                              <span key={documentId}>
                                {sourceIndex > 0 ? ", " : ""}
                                <Link
                                  className={styles.readerBackLink}
                                  href={
                                    `/campaigns/${campaignId}/documents/` +
                                    documentId
                                  }
                                >
                                  {document?.title?.trim() ||
                                    `Document ${documentId.slice(0, 8)}`}
                                </Link>
                              </span>
                            );
                          },
                        )}
                      </p>
                    </li>
                  ),
                )}
              </ul>
            </div>

            {brief.brief_json.open_questions.length > 0 ? (
              <div>
                <h3>Open questions</h3>
                <ul>
                  {brief.brief_json.open_questions.map((question) => (
                    <li key={question}>{question}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {brief.brief_json.recommended_follow_ups.length > 0 ? (
              <div>
                <h3>Recommended follow-ups</h3>
                <ul>
                  {brief.brief_json.recommended_follow_ups.map(
                    (followUp) => (
                      <li key={followUp}>{followUp}</li>
                    ),
                  )}
                </ul>
              </div>
            ) : null}

            {brief.brief_json.evidence_groups &&
            brief.brief_json.evidence_groups.length > 0 ? (
              <div>
                <h3>Evidence-group briefs</h3>
                <p className={styles.subtle}>
                  These partial briefs were generated from separate
                  relevance-ranked evidence groups before the final synthesis.
                </p>

                <div className={styles.documentGrid}>
                  {brief.brief_json.evidence_groups.map((group) => (
                    <details
                      className={styles.documentCard}
                      key={group.group_index}
                    >
                      <summary>
                        Group {group.group_index} ·{" "}
                        {group.input_document_count} source
                        {group.input_document_count === 1 ? "" : "s"} ·{" "}
                        {group.input_character_count.toLocaleString()} evidence
                        characters
                      </summary>

                      <p>
                        <strong>Group overview:</strong> {group.brief.overview}
                      </p>

                      <h4>Group findings</h4>
                      <ul>
                        {group.brief.key_findings.map(
                          (finding, findingIndex) => (
                            <li
                              key={
                                `${group.group_index}-` +
                                `${finding.statement}-${findingIndex}`
                              }
                            >
                              <p>{finding.statement}</p>
                              <p className={styles.subtle}>
                                Sources:{" "}
                                {finding.source_document_ids.map(
                                  (documentId, sourceIndex) => {
                                    const document = documents.find(
                                      (candidate) =>
                                        candidate.id === documentId,
                                    );

                                    return (
                                      <span key={documentId}>
                                        {sourceIndex > 0 ? ", " : ""}
                                        <Link
                                          className={styles.readerBackLink}
                                          href={
                                            `/campaigns/${campaignId}/documents/` +
                                            documentId
                                          }
                                        >
                                          {document?.title?.trim() ||
                                            `Document ${documentId.slice(0, 8)}`}
                                        </Link>
                                      </span>
                                    );
                                  },
                                )}
                              </p>
                            </li>
                          ),
                        )}
                      </ul>
                    </details>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </section>

    </main>
  );
}
