"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useOpenReviveCollectionId } from "../lib/openrevive-storage";
import styles from "./campaigns.module.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

type CrawlRunSummary = {
  id: string;
  collection_id: string;
  status: string;
  name: string | null;
  seed_urls: string[];
  research_intent: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  job_counts: Record<string, number>;
};

type CrawlRunList = {
  total: number;
  items: CrawlRunSummary[];
};

async function apiRequest<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
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

function campaignLabel(campaign: CrawlRunSummary): string {
  const name = campaign.name?.trim();

  if (name) {
    return name;
  }

  const intent = campaign.research_intent?.trim();

  if (intent) {
    return intent;
  }

  return campaign.seed_urls[0] ?? campaign.id;
}

function rootUrl(campaign: CrawlRunSummary): string {
  return campaign.seed_urls[0] ?? "No root URL";
}

export default function CampaignLibraryPage() {
  const collectionId = useOpenReviveCollectionId();
  const [campaigns, setCampaigns] = useState<CrawlRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshCampaigns = useCallback(
    async (silent = false) => {
      if (!collectionId) {
        return;
      }

      if (!silent) {
        setRefreshing(true);
      }

      try {
        const response = await apiRequest<CrawlRunList>(
          `/v1/collections/${collectionId}/crawl-runs`,
        );

        setCampaigns(response.items);
        setError(null);
      } catch (refreshError) {
        setError(
          refreshError instanceof Error
            ? refreshError.message
            : "Unable to load campaigns.",
        );
      } finally {
        setLoading(false);

        if (!silent) {
          setRefreshing(false);
        }
      }
    },
    [collectionId],
  );

  useEffect(() => {
    if (!collectionId) {
      return;
    }

    const initialRefresh = window.setTimeout(() => {
      void refreshCampaigns();
    }, 0);

    const timer = window.setInterval(() => {
      void refreshCampaigns(true);
    }, 3000);

    return () => {
      window.clearTimeout(initialRefresh);
      window.clearInterval(timer);
    };
  }, [collectionId, refreshCampaigns]);

  if (collectionId === null) {
    return (
      <main className={styles.page}>
        <section className={styles.emptyState}>
          <p className={styles.eyebrow}>OpenRevive / campaign library</p>
          <h1>No local research workspace found</h1>
          <p className={styles.subtle}>
            Create a campaign from the control plane first.
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
          <p className={styles.eyebrow}>OpenRevive / campaign library</p>
          <h1>Research campaigns</h1>
          <p className={styles.subtle}>
            {campaigns.length} stored campaign
            {campaigns.length === 1 ? "" : "s"} · refreshes every 3 seconds
          </p>
        </div>

        <div className={styles.headerActions}>
          <Link href="/" className={styles.primaryLink}>
            New campaign
          </Link>
          <button
            className={styles.secondaryButton}
            disabled={refreshing}
            onClick={() => void refreshCampaigns()}
            type="button"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {error ? <p className={styles.error}>{error}</p> : null}

      {loading ? (
        <section className={styles.emptyState}>
          <h2>Loading campaign library…</h2>
        </section>
      ) : null}

      {!loading && campaigns.length === 0 ? (
        <section className={styles.emptyState}>
          <h2>No campaigns yet</h2>
          <p className={styles.subtle}>
            Create a research campaign to begin collecting evidence.
          </p>
          <Link href="/" className={styles.primaryLink}>
            Create campaign
          </Link>
        </section>
      ) : null}

      <section className={styles.libraryGrid}>
        {campaigns.map((campaign) => {
          const counts = campaign.job_counts;
          const queued =
            (counts.PENDING ?? 0) + (counts.RETRY_PENDING ?? 0);
          const active = counts.LEASED ?? 0;

          return (
            <Link
              className={styles.campaignCard}
              href={`/campaigns/${campaign.id}`}
              key={campaign.id}
            >
              <div className={styles.cardHeader}>
                <span className={styles.status}>{campaign.status}</span>
                <span className={styles.cardDate}>
                  {formatDate(campaign.created_at)}
                </span>
              </div>

              <h2 className={styles.campaignTitle}>
                {campaignLabel(campaign)}
              </h2>

              <p className={styles.cardUrl}>{rootUrl(campaign)}</p>

              <div className={styles.countRow}>
                <span>
                  <strong>{counts.TOTAL ?? 0}</strong> jobs
                </span>
                <span>
                  <strong>{counts.SUCCEEDED ?? 0}</strong> fetched
                </span>
                <span>
                  <strong>{active}</strong> active
                </span>
                <span>
                  <strong>{queued}</strong> queued
                </span>
              </div>
            </Link>
          );
        })}
      </section>
    </main>
  );
}
