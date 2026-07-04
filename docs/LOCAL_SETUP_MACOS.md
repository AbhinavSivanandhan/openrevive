# Local Setup on macOS

This guide takes a fresh macOS checkout from clone to a working local OpenRevive stack.

It covers local development only. Running the stack does not require AWS, Terraform, Vercel, or Bedrock access. The repository bootstrap installs cloud tooling because the same repository also supports the AWS demo deployment.

For AWS deployment and teardown, see [`OPERATIONS.md`](OPERATIONS.md). For the system design and crawler behavior, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 1. What runs locally

Docker Compose starts this development topology:

```text
Browser
  |
  v
Next.js web application                http://localhost:3000
  |
  | /api/* rewrite through Docker network
  v
FastAPI API                             http://localhost:8000
  |
  v
PostgreSQL + pgvector                   localhost:5432

Crawler worker
  |                 |
  v                 v
PostgreSQL       MinIO S3-compatible storage
                 http://localhost:9000
                 console: http://localhost:9001

Redis                                    localhost:6379
```

| Service      | Local role                                                                                                                |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `web`        | Next.js development server. Browser requests to `/api/*` are rewritten to the API container.                              |
| `api`        | FastAPI control plane, API routes, health endpoints, and explicit campaign-brief generation.                              |
| `worker`     | Separate crawler process. It continuously polls PostgreSQL for eligible crawl jobs.                                       |
| `postgres`   | Durable local application state and a dedicated `openrevive_test` database. Includes `pgvector` and `pg_trgm` extensions. |
| `minio`      | S3-compatible object storage for raw fetched HTML artifacts.                                                              |
| `minio-init` | One-time initialization container that creates the configured artifact bucket and removes public bucket access.           |
| `redis`      | Available for future extensions. It is not on the current crawler, lease, queue, evidence, or briefing path.              |

The local worker runs continuously. AWS uses the same worker code but launches finite Fargate tasks that exit after the durable frontier is idle.

## 2. Required software

The local stack requires:

* macOS;
* Homebrew;
* Docker Desktop;
* Git;
* Node.js and pnpm;
* Python 3.14 and `uv`.

The repository’s bootstrap also installs Terraform, AWS CLI, `jq`, `pre-commit`, and `gitleaks` because they are required for the cloud deployment workflow.

Vercel CLI is not required for local development and is not installed by the repository `Brewfile`.

Install Apple command-line tools when macOS prompts for them:

```bash id="vhqd7z"
xcode-select --install
```

Install Homebrew before running the repository bootstrap. Docker Desktop must be installed and running before Compose commands can work.

## 3. Clone and bootstrap

```bash id="qbzp8v"
git clone <repository-url>
cd openrevive

make setup
make verify
```

`make setup` runs:

```text id="atdzue"
./scripts/bootstrap-macos.sh
```

The bootstrap script:

1. verifies that the host is macOS;
2. requires Homebrew;
3. installs missing tools from `Brewfile` without upgrading unrelated packages;
4. creates `.env` from `.env.example` when `.env` does not already exist.

`make verify` checks whether these commands are available:

```text id="ct1y3p"
git
uv
node
pnpm
terraform
aws
docker
jq
gitleaks
```

It also prints the installed Docker Compose version.

### Repository configuration prerequisite

`make setup` expects a tracked root-level `.env.example` file.

A clean checkout without `.env.example` is incomplete: the bootstrap script cannot create `.env`, and Docker Compose cannot resolve its required PostgreSQL, MinIO, and artifact-bucket variables.

The tracked `.env.example` should contain safe local-development values similar to:

```bash id="7vuaw6"
POSTGRES_DB=openrevive
POSTGRES_USER=openrevive
POSTGRES_PASSWORD=openrevive_local_password

MINIO_ROOT_USER=openrevive
MINIO_ROOT_PASSWORD=openrevive_local_minio_password
S3_BUCKET=openrevive-local-artifacts

BASIC_AUTH_ENABLED=false
```

These are local Docker credentials only. Do not reuse cloud, production, personal, or shared-team passwords in this file.

The current Compose stack requires these variables:

| Variable              | Used by                             |
| --------------------- | ----------------------------------- |
| `POSTGRES_DB`         | PostgreSQL, API, worker             |
| `POSTGRES_USER`       | PostgreSQL, API, worker             |
| `POSTGRES_PASSWORD`   | PostgreSQL, API, worker             |
| `MINIO_ROOT_USER`     | MinIO, MinIO initialization, worker |
| `MINIO_ROOT_PASSWORD` | MinIO, MinIO initialization, worker |
| `S3_BUCKET`           | MinIO initialization and worker     |
| `BASIC_AUTH_ENABLED`  | API and web, optional locally       |

The API and worker database URLs are derived inside Compose. Do not add a host-based `DATABASE_URL` to `.env` for the normal Docker workflow.

## 4. Start the local stack

First, start Docker Desktop:

```bash id="7hjvly"
open -a Docker
docker info
```

Then start OpenRevive:

```bash id="pz9puo"
make dev-up
```

This runs:

```text id="dj2vxh"
docker compose up --build
```

It builds local images when necessary and attaches Compose logs to the current terminal.

Keep this terminal open. Use another terminal for migrations, health checks, logs, tests, and normal development commands.

### Expected local endpoints

| Service               | URL                                  |
| --------------------- | ------------------------------------ |
| OpenRevive UI         | `http://localhost:3000`              |
| FastAPI API           | `http://localhost:8000`              |
| OpenAPI documentation | `http://localhost:8000/docs`         |
| API health            | `http://localhost:8000/health`       |
| API readiness         | `http://localhost:8000/health/ready` |
| MinIO S3 endpoint     | `http://localhost:9000`              |
| MinIO Console         | `http://localhost:9001`              |
| PostgreSQL            | `localhost:5432`                     |
| Redis                 | `localhost:6379`                     |

The MinIO Console uses the values in `.env`:

```text id="iy7wc0"
MINIO_ROOT_USER
MINIO_ROOT_PASSWORD
```

### Expected container state

After startup:

```bash id="ifp3l3"
make dev-ps
```

Expected behavior:

* `api`, `web`, `worker`, `postgres`, `redis`, and `minio` should be running.
* `minio-init` should finish with exit code `0`. This is expected: it creates the local artifact bucket and exits.
* PostgreSQL should become healthy before the API starts.
* The web container waits for the API health check.

## 5. Apply database migrations

The API container does **not** automatically run Alembic migrations.

After the stack is running, apply the application schema:

```bash id="e2hwe9"
docker compose exec api \
  sh -lc 'uv run --frozen alembic upgrade head'
```

Confirm the current migration revision:

```bash id="rtibgq"
docker compose exec api \
  sh -lc 'uv run --frozen alembic current'

docker compose exec api \
  sh -lc 'uv run --frozen alembic heads'
```

The `current` revision should match the available `heads` revision.

Health endpoints prove that the API process can connect to PostgreSQL. They do not prove that database tables have been migrated. Apply migrations before creating campaigns or running backend tests.

## 6. Verify the local stack

Run:

```bash id="vvzwb0"
make dev-ps

curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/ready
curl -fsS http://localhost:3000/api/health/ready
```

Expected responses include:

```json id="ngbfw6"
{"status":"ok"}
```

and:

```json id="m53ozj"
{"status":"ready","database":"connected"}
```

The final command verifies the full local frontend proxy path:

```text id="3hdmg4"
host browser or curl
  -> localhost:3000
  -> Next.js /api/* rewrite
  -> API container
  -> PostgreSQL readiness check
```

Open the UI:

```text id="hpwwxd"
http://localhost:3000
```

## 7. Run a local campaign

The browser control plane automatically creates a local demo workspace and collection on the first campaign created in that browser.

The IDs are stored in browser local storage under:

```text id="0k096u"
openrevive.demo.collection-id
```

Create a campaign from the UI:

1. Open `http://localhost:3000`.
2. Enter a campaign name.
3. Enter one public HTTP or HTTPS root URL.
4. Enter a concrete research intent.
5. Select **Create and open campaign**.
6. Watch the campaign workspace refresh while the worker processes the durable frontier.
7. Open a fetched document after the campaign reaches a terminal state.

The browser UI uses this fixed demo profile:

```text id="w9p6tz"
Maximum pages:    50
Maximum depth:    2
Request timeout:  20 seconds
Maximum attempts: 2
```

The root URL hostname becomes the browser UI’s allowed-domain scope.

A suitable local seed is:

```text id="u9v75q"
https://docs.python.org/3/library/asyncio.html
```

Use a specific research intent, for example:

```text id="dn4fce"
Explain event loops, task groups, cancellation, and structured concurrency.
```

### Local behavior without Bedrock credentials

A normal local crawl does not require AWS credentials.

When a depth-zero seed page has a research intent, the worker attempts bounded Bedrock-assisted link selection. Without valid AWS credentials or Bedrock model access:

* the root page can still fetch and persist successfully;
* the worker logs a frontier-selection warning;
* no AI-selected child pages are added;
* the campaign can complete with only the root document.

This is expected local behavior.

The explicit campaign-brief action does require Amazon Bedrock. Without valid Bedrock credentials and Nova Micro access, the brief is stored as failed and can be retried later after correcting cloud credentials.

## 8. Local configuration model

Docker Compose passes container-specific configuration directly from `.env`.

### API configuration

The API receives:

```text id="m3kw7m"
DATABASE_URL=postgresql+asyncpg://<user>:<password>@postgres:5432/<database>
TEST_DATABASE_URL=postgresql+asyncpg://<user>:<password>@postgres:5432/openrevive_test
```

The container hostname is `postgres`, not `localhost`.

### Worker configuration

The worker receives:

```text id="aj6wid"
DATABASE_URL=postgresql+asyncpg://<user>:<password>@postgres:5432/<database>

WORKER_LEASE_SECONDS=180
WORKER_IDLE_POLL_SECONDS=1.0
WORKER_MAX_RESPONSE_BYTES=2000000

S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET=<S3_BUCKET from .env>
S3_ACCESS_KEY_ID=<MINIO_ROOT_USER from .env>
S3_SECRET_ACCESS_KEY=<MINIO_ROOT_PASSWORD from .env>
S3_REGION_NAME=us-east-1
```

The local worker differs from the cloud worker in two important ways:

| Behavior               | Local Compose      | AWS Fargate                                |
| ---------------------- | ------------------ | ------------------------------------------ |
| Idle behavior          | Polls continuously | Exits after two consecutive idle polls     |
| Maximum response bytes | 2 MB               | 4 MB application default unless overridden |
| Artifact storage       | MinIO              | Amazon S3                                  |
| Job source             | PostgreSQL         | PostgreSQL                                 |

### Web configuration

The web container receives:

```text id="cuqnza"
API_INTERNAL_URL=http://api:8000
```

The web container hostname is `api`, not `localhost`.

The browser never needs to know that Docker-internal hostname. It sends requests to `/api/*` on `localhost:3000`; Next.js performs the rewrite inside the container network.

### Local Basic Auth

Basic Auth is disabled locally by default:

```text id="x4wxo9"
BASIC_AUTH_ENABLED=false
```

To test the private-access behavior locally, set all required values in `.env`:

```bash id="m0xmu3"
BASIC_AUTH_ENABLED=true
BASIC_AUTH_USERNAME=<local-only username>
BASIC_AUTH_PASSWORD=<local-only password>
```

Then recreate containers so Compose applies the changed environment:

```bash id="d09gco"
make dev-down
make dev-up
```

Do not use the cloud Basic Auth password in local `.env`.

## 9. Run tests and checks

### Backend tests

The test suite uses the dedicated Compose database:

```text id="cjq54v"
openrevive_test
```

Before the first test run on a fresh PostgreSQL volume, migrate that database:

```bash id="zq8czv"
docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen alembic upgrade head'
```

Then run the backend test suite:

```bash id="zpuekn"
docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen pytest -q'
```

The test fixture refuses to run against a database whose name does not end in `_test`.

Never run the test suite against the normal local application database or any non-test database.

### Frontend lint

Run the frontend lint command inside the web container:

```bash id="t4i19w"
docker compose exec web pnpm lint
```

### API contract exploration

FastAPI exposes interactive OpenAPI documentation locally:

```text id="phqnag"
http://localhost:8000/docs
```

Use it to inspect request bodies, response schemas, and lifecycle endpoints while developing API features.

## 10. Local lifecycle commands

```bash id="g01t7q"
make setup
make verify

make dev-up
make dev-down
make dev-reset
make dev-logs
make dev-ps
```

| Command          | Effect                                                                                                                        |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `make setup`     | Installs missing macOS tools from the `Brewfile` and creates `.env` from `.env.example` when necessary.                       |
| `make verify`    | Checks that required local development commands are available.                                                                |
| `make dev-up`    | Builds and starts Compose services in the foreground.                                                                         |
| `make dev-down`  | Stops and removes containers and the Compose network while preserving named volumes.                                          |
| `make dev-reset` | Stops containers and deletes named volumes, including PostgreSQL, MinIO, Redis, Python virtualenv, and frontend dependencies. |
| `make dev-logs`  | Follows the most recent 200 log lines from all Compose services.                                                              |
| `make dev-ps`    | Shows Compose service status.                                                                                                 |

Use `make dev-down` for normal shutdown.

Use `make dev-reset` only when all local PostgreSQL data, MinIO artifacts, Redis data, and installed container volumes can be discarded.

After `make dev-reset`, start the stack and reapply both application and test migrations:

```bash id="iyl1hn"
make dev-up

# In a second terminal:
docker compose exec api \
  sh -lc 'uv run --frozen alembic upgrade head'

docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen alembic upgrade head'
```

## 11. Troubleshooting

### Docker Desktop is not running

```bash id="a6d7d1"
open -a Docker
docker info
```

Wait until Docker Desktop reports that its engine is running, then retry:

```bash id="9dz0g7"
make dev-up
```

### A host port is already in use

Check the expected local ports:

```bash id="mk2m5r"
lsof -nP -iTCP:3000 -sTCP:LISTEN
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:6379 -sTCP:LISTEN
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:9000 -sTCP:LISTEN
lsof -nP -iTCP:9001 -sTCP:LISTEN
```

Stop the conflicting local process or change the relevant host-side port mapping in `compose.yaml`.

### `make setup` fails because `.env.example` does not exist

Restore the tracked root `.env.example` file before proceeding.

The bootstrap script explicitly runs:

```text id="wox3ga"
cp .env.example .env
```

This is a repository configuration issue, not a Docker issue.

### API reports that a table does not exist

The database is reachable but the schema has not been migrated.

Check migration state:

```bash id="cu0ir5"
docker compose exec api \
  sh -lc 'uv run --frozen alembic current'

docker compose exec api \
  sh -lc 'uv run --frozen alembic heads'
```

Apply migrations:

```bash id="rovl5x"
docker compose exec api \
  sh -lc 'uv run --frozen alembic upgrade head'
```

### Tests fail with missing tables

The dedicated test database needs its own migration:

```bash id="wx1m12"
docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen alembic upgrade head'
```

Then rerun:

```bash id="wmyci7"
docker compose exec api \
  sh -lc 'DATABASE_URL="$TEST_DATABASE_URL" uv run --frozen pytest -q'
```

### PostgreSQL ignores changed `.env` credentials

PostgreSQL initialization credentials apply only when its data volume is first created.

For disposable local data:

```bash id="7pbxpm"
make dev-reset
make dev-up
```

Then reapply migrations.

### MinIO artifact persistence fails

Check that MinIO and bucket initialization completed:

```bash id="la3vqg"
docker compose logs --tail=200 minio
docker compose logs --tail=200 minio-init
```

Expected `minio-init` output ends with:

```text id="diwacb"
MinIO bucket is ready.
```

Also confirm that `S3_BUCKET`, `MINIO_ROOT_USER`, and `MINIO_ROOT_PASSWORD` are present and consistent in `.env`.

### Campaign library says no local workspace exists after a reset

`make dev-reset` deletes PostgreSQL state but does not clear browser local storage.

Remove the stale collection ID in the browser console:

```js id="apbu1l"
localStorage.removeItem("openrevive.demo.collection-id")
```

Refresh `http://localhost:3000`, then create a campaign again. The UI creates a new demo workspace and collection automatically.

### The worker logs Bedrock selection failures

This is expected when local Docker does not have valid AWS credentials or Nova Micro access.

The root page can still be crawled and stored. Bedrock failures affect AI-assisted child selection and explicit campaign briefs, not basic local fetch and evidence persistence.

### View one service’s logs

```bash id="2x3ii5"
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 worker
docker compose logs -f --tail=200 web
docker compose logs -f --tail=200 postgres
docker compose logs -f --tail=200 minio
docker compose logs -f --tail=200 minio-init
```

## 12. Local security rules

Do not commit:

```text id="8frk3q"
.env
.env.local
infra/.local/
apps/web/.vercel/
Terraform state files
AWS credentials
database dumps
MinIO exports
Basic Auth credentials
```

The repository `.gitignore` excludes the main local and cloud credential paths, but do not rely solely on ignore rules. Check staged changes before committing:

```bash id="a3zrxh"
git status --short
git diff --cached --name-only
```

Use only non-production credentials in local Docker configuration.

## 13. Next steps

After local setup is working:

* read [`USER_FLOW_AND_USE_CASES.md`](USER_FLOW_AND_USE_CASES.md) for the browser workflow and campaign behavior;
* read [`ARCHITECTURE.md`](ARCHITECTURE.md) before changing crawler state, leases, domain pacing, evidence storage, or Bedrock integration;
* read [`OPERATIONS.md`](OPERATIONS.md) before deploying the AWS demo stack.
