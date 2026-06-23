# Developer Setup

## Requirements

* macOS
* Homebrew
* Docker Desktop installed and running
* Git access to this repository

## Quick start

```bash
git clone <repository-url>
cd openrevive
make setup
```

`make setup` installs declared developer tools through Homebrew, creates a local `.env` file, and starts PostgreSQL, Redis, and MinIO.

Verify the local environment:

```bash
make verify
docker compose ps
```

## Local services

| Service               | Purpose                                 | Address                 |
| --------------------- | --------------------------------------- | ----------------------- |
| PostgreSQL + pgvector | Product data, search, future embeddings | `localhost:5432`        |
| Redis                 | Background job broker and cache         | `localhost:6379`        |
| MinIO                 | Local S3-compatible file storage        | `localhost:9000`        |
| MinIO Console         | Inspect local uploaded files            | `http://localhost:9001` |

## Local environment variables

The first setup creates `.env` from `.env.example`.

Do not commit `.env`. It contains local credentials and may later contain API keys.

## Common commands

```bash
make infra-up       # Start local PostgreSQL, Redis, and MinIO
make infra-down     # Stop local services but preserve data
make infra-reset    # Stop local services and delete local volumes/data
make infra-logs     # Stream local service logs
make verify         # Verify tools and local dependencies
```

## Resetting local data

Use this only when local data can be safely deleted:

```bash
make infra-reset
make infra-up
```

## Troubleshooting

If Docker is not available:

```bash
open -a Docker
docker info
```

If a local port is already in use, identify the process:

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:9000 -sTCP:LISTEN
```

# random note
minio-init does not appear in docker compose ps because it runs once, creates the bucket, and exits.

## Application services

Application services currently run from the host machine during development. Local dependencies run through Docker Compose.

| Service          | Purpose                                                 | Address                      |
| ---------------- | ------------------------------------------------------- | ---------------------------- |
| FastAPI API      | Product API and future background-command control plane | `http://localhost:8000`      |
| FastAPI API docs | Interactive OpenAPI / Swagger documentation             | `http://localhost:8000/docs` |
| Next.js web app  | Product user interface                                  | `http://localhost:3000`      |

## Run the FastAPI API

Ensure local infrastructure is running first:

```bash
make infra-up
```

Start the API from a separate terminal:

```bash
cd services/api
uv run fastapi dev app/main.py --port 8000
```

Verify it:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Stop the API with:

```text
Ctrl+C
```

## Current local startup sequence

```bash
git clone <repository-url>
cd openrevive
make setup
make infra-up

# Terminal 1
cd services/api
uv run fastapi dev app/main.py --port 8000
```
### `uv: command not found`

Run the project bootstrap again:

```bash
./scripts/bootstrap-macos.sh
```

Then verify:

```bash
uv --version
```

## Run the web app

Start the API first if you want the full local stack.

```bash
# Terminal 1
cd services/api
uv run fastapi dev app/main.py --port 8000
