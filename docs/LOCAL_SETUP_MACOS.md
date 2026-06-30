# Local Setup on macOS

This guide takes a fresh MacBook from repository clone to a working local OpenRevive stack.

## What runs locally

Docker Compose runs:

```text
Next.js web application        http://localhost:3000
FastAPI API                    http://localhost:8000
FastAPI OpenAPI docs           http://localhost:8000/docs
PostgreSQL + pgvector          localhost:5432
Redis                          localhost:6379
MinIO S3-compatible storage    http://localhost:9000
MinIO Console                  http://localhost:9001
Crawler worker                 no public port
```

The worker is a separate process from the API. Locally it polls continuously; the cloud deployment uses the same worker code but exits when the durable queue drains.

## Requirements

Install or make available:

- macOS with command-line developer tools;
- Git;
- Homebrew;
- Docker Desktop;
- Node.js and pnpm;
- Python 3.14 and uv;
- Terraform;
- AWS CLI for cloud deployment;
- Vercel CLI for frontend deployment.

The repository bootstrap command installs or verifies the project toolchain through the declared Homebrew configuration.

## Clone and bootstrap

```bash
git clone <repository-url>
cd openrevive

make setup
make verify
```

`make setup` runs the macOS bootstrap script.

It creates `.env` from `.env.example` when needed and installs or verifies development tools.

Do not commit `.env`, `.env.local`, Terraform state, AWS credentials, database dumps, or generated Vercel configuration.

## Start local development

```bash
make dev-up
```

This command builds the containers and streams logs in the foreground.

Open a second terminal and verify:

```bash
make dev-ps

curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:3000/api/health/ready
```

Expected health responses are HTTP `200`.

Open the local UI:

```text
http://localhost:3000
```

## Run a local campaign

1. Open the local UI.
2. Create a workspace.
3. Create a collection.
4. Create a campaign with a small technical documentation seed.
5. Use one allowed domain.
6. Start with `max_pages=1` and `max_depth=0`.
7. Start the campaign.
8. Watch the run reach a terminal state.
9. Open the resulting document.

A suitable local smoke configuration:

```text
Seed URL:
https://docs.python.org/3/library/asyncio.html

Allowed domain:
docs.python.org

Maximum pages:
1

Maximum depth:
0
```

## Local lifecycle commands

```bash
make dev-up
make dev-down
make dev-reset
make dev-logs
make dev-ps
make verify
```

`make dev-down` preserves Docker volumes.

`make dev-reset` deletes local containers and volumes. Use it only when local PostgreSQL and MinIO data can be discarded.

## Run backend tests

The test suite uses the dedicated Compose test database.

```bash
docker compose exec api \
  sh -c 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen pytest -q'
```

Never point tests at a non-test database.

## Local troubleshooting

### Docker Desktop is not running

```bash
open -a Docker
docker info
```

### A local port is occupied

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:9000 -sTCP:LISTEN
```

### View one service's logs

```bash
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 worker
docker compose logs -f --tail=200 web
docker compose logs -f --tail=200 postgres
```

### Local database credentials changed but Postgres still uses old values

Postgres initialization values apply only to a fresh volume.

For disposable local data:

```bash
make dev-reset
make dev-up
```

## AWS deployment from a MacBook

The AWS backend uses:

```text
ALB -> ECS Fargate API -> Aurora PostgreSQL
                         -> SQS -> EventBridge Pipe -> finite Fargate worker
                         -> S3 artifacts
```

Configure an AWS CLI profile named `openrevive`:

```bash
aws configure --profile openrevive
aws sts get-caller-identity --profile openrevive
```

Deploy:

```bash
make cloud-up
```

Verify:

```bash
make cloud-status
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
```

The deployment creates `infra/.local/cloud.env` locally for deployment configuration. It is ignored by Git.

## Vercel deployment from a MacBook

The frontend is deployed separately from AWS.

The deployed frontend uses a Vercel rewrite:

```text
/api/*
  -> AWS ALB API origin
```

Deploying the backend creates or changes the ALB origin. When the ALB origin changes, update the `/api` rewrite destination in `apps/web/vercel.json`, then deploy the frontend again.

Typical Vercel flow:

```bash
cd apps/web

vercel whoami
vercel link
vercel deploy --prod
```

The frontend deployment should be verified with:

```bash
curl -fsS https://<your-vercel-domain>/api/health
```

The browser should use the Vercel `/api` path rather than calling the HTTP ALB origin directly.
