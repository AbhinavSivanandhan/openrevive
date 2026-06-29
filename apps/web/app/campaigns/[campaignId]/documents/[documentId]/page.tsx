"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useOpenReviveCollectionId } from "../../../../lib/openrevive-storage";
import styles from "../../../campaigns.module.css";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

type CrawledDocumentDetail = {
  id: string;
  crawl_job_id: string;
  source_url: string;
  original_url: string;
  title: string | null;
  extracted_text: string | null;
  raw_object_key: string;
  content_type: string;
  created_at: string;
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
      typeof (body as { detail?: unknown }).detail === "string"
    ) {
      throw new Error(
        (body as { detail: string }).detail,
      );
    }

    throw new Error(`Request failed with HTTP ${response.status}.`);
  }

  return response.json() as Promise<T>;
}


export default function DocumentReaderPage() {
  const { campaignId, documentId } = useParams<{
    campaignId: string;
    documentId: string;
  }>();

  const [document, setDocument] =
    useState<CrawledDocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const collectionId = useOpenReviveCollectionId();

  useEffect(() => {
    if (!collectionId) {
      return;
    }

    let cancelled = false;

    async function loadDocument() {
      try {
        const response = await apiRequest<CrawledDocumentDetail>(
          `/v1/collections/${collectionId}/crawl-runs/` +
            `${campaignId}/documents/${documentId}`,
        );

        if (!cancelled) {
          setDocument(response);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Unable to load this crawled document.",
          );
        }
      }
    }

    void loadDocument();

    return () => {
      cancelled = true;
    };
  }, [campaignId, collectionId, documentId]);

  const displayError =
    collectionId === null
      ? "No OpenRevive collection is configured in this browser."
      : error;

  return (
    <main className={styles.page}>
      <section className={styles.section}>
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>Persisted evidence</p>
            <h1>{document?.title ?? "Crawled document"}</h1>
          </div>

          <Link
            className={styles.readerBackLink}
            href={`/campaigns/${campaignId}`}
          >
            ← Campaign workspace
          </Link>
        </div>

        {displayError ? (
          <p className={styles.error}>{displayError}</p>
        ) : null}

        {!displayError && !document ? (
          <p className={styles.subtle}>Loading persisted content…</p>
        ) : null}

        {document ? (
          <>
            <div className={styles.readerMeta}>
              <div>
                <strong>Crawled URL</strong>
                <code>{document.source_url}</code>
              </div>

              <div>
                <strong>Captured</strong>
                <span>
                  {new Date(document.created_at).toLocaleString()}
                </span>
              </div>

              <div>
                <strong>Content type</strong>
                <span>{document.content_type}</span>
              </div>
            </div>

            <div className={styles.readerActions}>
              <a
                href={document.original_url}
                rel="noreferrer"
                target="_blank"
              >
                Open original page ↗
              </a>

              <span className={styles.subtle}>
                Raw artifact: {document.raw_object_key}
              </span>
            </div>

            <article className={styles.readerBody}>
              {document.extracted_text ??
                "No extracted text was persisted for this page."}
            </article>
          </>
        ) : null}
      </section>
    </main>
  );
}
