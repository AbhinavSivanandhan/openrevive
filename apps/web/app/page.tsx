"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import styles from "./control.module.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

const STORAGE_KEYS = {
  collectionId: "openrevive.demo.collection-id",
};

type Workspace = {
  id: string;
};

type Collection = {
  id: string;
};

type CrawlRunCreateResponse = {
  id: string;
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
      "detail" in body
    ) {
      const detail = (body as { detail?: unknown }).detail;

      if (typeof detail === "string") {
        throw new Error(detail);
      }
    }

    throw new Error(`Request failed with HTTP ${response.status}.`);
  }

  return response.json() as Promise<T>;
}

function parseCampaignRootUrl(value: string): {
  originalUrl: string;
  allowedDomain: string;
} {
  const originalUrl = value.trim();
  const parsed = new URL(originalUrl);

  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("Campaign root URL must use http or https.");
  }

  if (!parsed.hostname) {
    throw new Error("Campaign root URL must include a hostname.");
  }

  return {
    originalUrl,
    allowedDomain: parsed.hostname.toLowerCase(),
  };
}

export default function ControlPlanePage() {
  const [campaignName, setCampaignName] = useState("");
  const [rootUrl, setRootUrl] = useState("");
  const [researchIntent, setResearchIntent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ensureDemoCollection(): Promise<string> {
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
  }

  async function startCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const name = campaignName.trim();
    const intent = researchIntent.trim();

    if (!name) {
      setError("Give this campaign a short name.");
      return;
    }

    if (!intent) {
      setError("Describe the research objective.");
      return;
    }

    let campaignRoot: {
      originalUrl: string;
      allowedDomain: string;
    };

    try {
      campaignRoot = parseCampaignRootUrl(rootUrl);
    } catch (urlError) {
      setError(
        urlError instanceof Error
          ? urlError.message
          : "Enter a valid campaign root URL.",
      );
      return;
    }

    setSubmitting(true);

    try {
      const collectionId = await ensureDemoCollection();

      const campaign = await apiRequest<CrawlRunCreateResponse>(
        `/v1/collections/${collectionId}/crawl-runs`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": crypto.randomUUID(),
          },
          body: JSON.stringify({
            name,
            seed_urls: [campaignRoot.originalUrl],
            allowed_domains: [campaignRoot.allowedDomain],
            research_intent: intent,
            max_pages: 50,
            max_depth: 2,
            request_timeout_seconds: 20,
            max_attempts: 2,
          }),
        },
      );

      await apiRequest(
        `/v1/collections/${collectionId}/crawl-runs/` +
          `${campaign.id}/start`,
        {
          method: "POST",
        },
      );

      window.location.assign(`/campaigns/${campaign.id}`);
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to create the campaign.",
      );
      setSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link className={styles.brand} href="/">
          <span className={styles.brandMark}>O</span>
          <span>
            <strong>OpenRevive</strong>
            <small>Crawler-first research workspace</small>
          </span>
        </Link>

        <nav className={styles.nav}>
          <Link className={styles.secondaryLink} href="/campaigns">
            Campaigns
          </Link>
        </nav>
      </header>

      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>Campaign control plane</p>
          <h1>Start a focused research crawl.</h1>
          <p>
            Define one root source and one research objective. OpenRevive
            expands a bounded, priority-ranked frontier and takes you to the
            live campaign workspace immediately.
          </p>

          <div className={styles.boundaryNote}>
            <span>Bounded live-demo profile</span>
            <strong>50 pages · depth 2 · 20 second requests</strong>
          </div>
        </div>

        <form className={styles.form} onSubmit={startCampaign}>
          <div className={styles.formHeading}>
            <p className={styles.eyebrow}>New campaign</p>
            <h2>Research brief</h2>
          </div>

          <label className={styles.field}>
            <span>Campaign name</span>
            <input
              autoComplete="off"
              className={styles.textInput}
              maxLength={160}
              onChange={(event) => setCampaignName(event.target.value)}
              placeholder="Asyncio Runtime Internals"
              type="text"
              value={campaignName}
            />
            <small>
              A short label used in the campaign library and workspace.
            </small>
          </label>

          <label className={styles.field}>
            <span>Campaign root URL</span>
            <input
              autoComplete="url"
              className={styles.textInput}
              onChange={(event) => setRootUrl(event.target.value)}
              placeholder="https://docs.python.org/3/library/asyncio.html"
              type="url"
              value={rootUrl}
            />
            <small>One starting URL. Its hostname becomes the crawl scope.</small>
          </label>

          <label className={styles.field}>
            <span>Research intent</span>
            <textarea
              className={styles.intentInput}
              maxLength={500}
              onChange={(event) => setResearchIntent(event.target.value)}
              placeholder={
                "Event loops, task groups, cancellation, " +
                "and concurrency patterns"
              }
              value={researchIntent}
            />
            <small>
              This guides frontier ranking. More concrete technical terms give
              the crawler better signals.
            </small>
          </label>

          {error ? <p className={styles.error}>{error}</p> : null}

          <button
            className={styles.primaryButton}
            disabled={submitting}
            type="submit"
          >
            {submitting ? "Creating campaign…" : "Create and open campaign"}
          </button>
        </form>
      </section>
    </main>
  );
}
