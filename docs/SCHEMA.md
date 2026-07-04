--
-- PostgreSQL database dump
--

\restrict HUdjtzw05INPHqSaG5A0vvzLopuletcPSwXJFDRZVr2pK83A0cMBtToEnuDgE0u

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg12+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);

--
-- Name: collections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.collections (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    name character varying(160) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: crawl_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crawl_jobs (
    id uuid NOT NULL,
    crawl_run_id uuid NOT NULL,
    parent_job_id uuid,
    original_url text NOT NULL,
    normalized_url text NOT NULL,
    domain character varying(255) NOT NULL,
    depth integer NOT NULL,
    status character varying(32) DEFAULT 'PENDING'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer NOT NULL,
    lease_owner character varying(128),
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_message text,
    http_status_code integer,
    fetched_bytes integer,
    fetch_duration_ms integer,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_claimed_by_worker_id character varying(128),
    anchor_text text,
    priority_score integer DEFAULT 0 NOT NULL,
    priority_band character varying(16) DEFAULT 'LOW'::character varying NOT NULL,
    discovery_reason text,
    CONSTRAINT ck_crawl_jobs_attempt_count_non_negative CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_crawl_jobs_depth_non_negative CHECK ((depth >= 0)),
    CONSTRAINT ck_crawl_jobs_max_attempts_positive CHECK ((max_attempts > 0))
);

--
-- Name: crawl_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crawl_runs (
    id uuid NOT NULL,
    collection_id uuid NOT NULL,
    status character varying(32) DEFAULT 'PENDING'::character varying NOT NULL,
    seed_urls jsonb NOT NULL,
    allowed_domains jsonb NOT NULL,
    max_pages integer NOT NULL,
    max_depth integer NOT NULL,
    request_timeout_seconds integer NOT NULL,
    max_attempts integer NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    research_intent text,
    name character varying(160),
    CONSTRAINT ck_crawl_runs_max_attempts_positive CHECK ((max_attempts > 0)),
    CONSTRAINT ck_crawl_runs_max_depth_non_negative CHECK ((max_depth >= 0)),
    CONSTRAINT ck_crawl_runs_max_pages_positive CHECK ((max_pages > 0)),
    CONSTRAINT ck_crawl_runs_request_timeout_positive CHECK ((request_timeout_seconds > 0))
);

--
-- Name: crawled_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crawled_documents (
    id uuid NOT NULL,
    crawl_job_id uuid NOT NULL,
    raw_object_key character varying(1024) NOT NULL,
    content_type character varying(255) NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    title text,
    extracted_text text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: worker_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_heartbeats (
    worker_id character varying(128) NOT NULL,
    status character varying(32) DEFAULT 'STARTING'::character varying NOT NULL,
    current_job_id uuid,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    last_heartbeat_at timestamp with time zone DEFAULT now() NOT NULL,
    stopped_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: workspaces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workspaces (
    id uuid NOT NULL,
    name character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Constraints and indexes omitted for brevity in this block, but present in your original dump.
--
-- PostgreSQL database dump complete
--

\unrestrict HUdjtzw05INPHqSaG5A0vvzLopuletcPSwXJFDRZVr2pK83A0cMBtToEnuDgE0u
