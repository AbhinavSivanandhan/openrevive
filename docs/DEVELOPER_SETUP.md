# Developer Setup

## Purpose

OpenRevive is a monorepo with independently deployable application services:

```text
apps/web        Next.js frontend
services/api    FastAPI API
```

For local development, Docker Compose runs the full application stack:

```text
web → api → postgres
          ↘ redis
          ↘ minio
```

The frontend and API run in containers with source-code bind mounts, so normal edits reload during development.

## Requirements

* macOS
* Homebrew
* Docker Desktop installed and running
* Git access to this repository

The bootstrap script installs the local developer tools declared in `Brewfile`, including Docker tooling, `uv`, Node.js, pnpm, Terraform, and supporting utilities.

## First-time setup

```bash
git clone <repository-url>
cd openrevive
make setup
```

`make setup`:

* runs `scripts/bootstrap-macos.sh`;
* installs declared developer tools through Homebrew;
* creates `.env` from `.env.example` when `.env` does not exist.

Do not commit `.env`. It contains local credentials and may later contain provider API keys.

Verify the local toolchain:

```bash
make verify
```

## Start the local stack

```bash
make dev-up
```

This builds and starts:

| Service               | Purpose                                 | Local address                |
| --------------------- | --------------------------------------- | ---------------------------- |
| Next.js web           | Product UI                              | `http://localhost:3000`      |
| FastAPI API           | Product API                             | `http://localhost:8000`      |
| FastAPI docs          | OpenAPI / Swagger UI                    | `http://localhost:8000/docs` |
| PostgreSQL + pgvector | Product data, search, future embeddings | `localhost:5432`             |
| Redis                 | Future job queue and cache              | `localhost:6379`             |
| MinIO                 | Local S3-compatible object storage      | `http://localhost:9000`      |
| MinIO Console         | Inspect local object storage            | `http://localhost:9001`      |

`make dev-up` runs Compose in the foreground and streams logs. Stop it with `Ctrl+C`.

## Verify the stack

Open a second terminal:

```bash
cd openrevive

make dev-ps

curl -i http://localhost:8000/health
curl -i http://localhost:8000/health/ready
curl -i http://localhost:3000/api/health/ready
```

All three requests should return `200 OK`.

These checks prove:

```text
localhost:8000/health
→ API container is reachable from the host.

localhost:8000/health/ready
→ API container can connect to PostgreSQL.

localhost:3000/api/health/ready
→ Next.js proxies browser-facing API requests to the API container.
```

## Daily commands

```bash
make dev-up      # build and start the full local stack
make dev-down    # stop containers; preserve local data volumes
make dev-reset   # stop containers and delete all local Docker data
make dev-logs    # follow logs from all services
make dev-ps      # show running services
make verify      # verify local developer tooling
```

Use `make dev-reset` only when local data can safely be discarded.

## Development reload behavior

The application source is bind-mounted into its containers.

* Editing files under `apps/web` triggers Next.js development reload.
* Editing files under `services/api` triggers FastAPI development reload.
* Re-run `make dev-up` after changing dependencies, Dockerfiles, Compose configuration, or service-level environment variables.

## Environment variables

The root `.env` file configures local infrastructure and host-side tools.

The API container overrides its database hostname internally:

```text
Host-side database URL: localhost:5432
Container-side database URL: postgres:5432
```

Inside Docker Compose, service names are network hostnames:

```text
postgres
redis
minio
api
web
```

`localhost` inside a container means that same container, not your Mac and not another Compose service.

## PostgreSQL initialization behavior

These variables are used only when Postgres initializes a fresh data volume:

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
```

Changing them in `.env` does not modify an existing database volume.

For a disposable local reset:

```bash
make dev-reset
make dev-up
```

Do not use this against data you need to preserve.

## Frontend structure

The frontend is self-contained:

```text
apps/web/
├─ app/
├─ public/
├─ package.json
├─ pnpm-lock.yaml
├─ pnpm-workspace.yaml
├─ next.config.ts
└─ Dockerfile
```

The frontend Docker build context is `apps/web` only.

`pnpm-workspace.yaml` in `apps/web` stores the approved build-script policy for required frontend dependencies. `pnpm-lock.yaml` records the exact frontend dependency graph.

## API structure

```text
services/api/
├─ app/
├─ pyproject.toml
├─ uv.lock
└─ Dockerfile
```

The API uses Python 3.14, FastAPI, SQLAlchemy async support, asyncpg, and pydantic-settings.

## Troubleshooting

### Docker is unavailable

```bash
open -a Docker
docker info
```

### A required local port is already occupied

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:9000 -sTCP:LISTEN
```

### View service logs

```bash
make dev-logs
```

Or inspect one service:

```bash
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 web
docker compose logs -f --tail=200 postgres
```

### MinIO initialization

`minio-init` is a one-shot Compose service. It creates the local bucket and exits, so it does not normally appear in `docker compose ps`.
