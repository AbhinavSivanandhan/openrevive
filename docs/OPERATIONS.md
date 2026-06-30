# OpenRevive Operations Runbook

This document covers the deployed AWS demo environment and the repeatable commands used to verify, operate, pause, and remove it.

## Environment model

```text
Vercel frontend
  -> /api rewrite
  -> AWS Application Load Balancer
  -> ECS Fargate API service
  -> Aurora PostgreSQL Serverless v2

API start/resume action
  -> SQS wake-up event
  -> EventBridge Pipe
  -> one-shot ECS Fargate crawler worker
  -> Aurora + S3 artifacts
```

The API service is long-running while the environment is active.

Crawler workers are not permanent services. A worker is launched after a campaign wake-up event, claims available PostgreSQL jobs, processes work, then exits after it observes two idle polling cycles.

## Normal cloud lifecycle

### Deploy or update

```bash
make cloud-up
```

This command:

1. Applies the AWS foundation layer.
2. Builds and pushes the ARM64 API/worker image to ECR.
3. Applies runtime infrastructure with the API initially stopped.
4. Runs Alembic migrations as a one-shot ECS task.
5. Starts the API ECS service.
6. Performs an API health check.

Expected final output includes:

```text
Deployment complete.
API: http://...
Queue: https://sqs...
```

### View deployed status

```bash
make cloud-status
```

This reports:

- Aurora cluster identifier.
- S3 artifact bucket.
- SQS queue URL.
- ECS API desired/running task counts.
- EventBridge Pipe state.
- Approximate queue depth.
- API base URL.

### Follow logs

```bash
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
```

Use API logs for request failures, startup failures, and database configuration problems.

Use worker logs for crawl execution, fetch failures, lease behavior, and confirmation that finite workers drain and exit.

### Pause normal activity

```bash
make cloud-stop
```

This is an alias for `make cloud-kill`.

It stops new worker launches, scales the API service to zero, and stops currently running demo tasks.

It does not delete Aurora, S3 artifacts, ECR images, SQS messages, or Terraform state.

### Resume the environment

```bash
make cloud-resume
```

This scales the API service back to one task and starts the EventBridge Pipe.

### Emergency cost-control action

```bash
make cloud-kill
```

Use this when you want compute to stop immediately.

It:

```text
1. Stops the EventBridge Pipe.
2. Scales the API service to zero.
3. Stops running ECS tasks.
```

Aurora, S3, ECR, SQS, IAM, and CloudWatch remain.

## Verification suite

### Read-only health and infrastructure check

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
```

`cloud-check` creates no campaign data and changes no resources.

It verifies:

- ALB to API health.
- ECS API desired and running count.
- Aurora availability.
- EventBridge Pipe state.
- SQS queue attributes.
- S3 lifecycle rule.
- ECR image availability.
- AWS Budget resource.
- Optional Vercel to AWS API proxy.

A successful run ends with:

```text
PASS: cloud-check completed.
```

### Full end-to-end smoke test

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
```

`cloud-smoke` runs `cloud-check` first, then creates an isolated one-page crawl campaign.

It verifies:

```text
API
  -> Aurora CrawlRun and CrawlJob records
  -> SQS wake-up event
  -> EventBridge Pipe
  -> Fargate worker task
  -> external page fetch
  -> S3 raw artifact
  -> Aurora CrawledDocument
  -> worker drain and exit log
```

A successful run ends with:

```text
PASS: crawl reached SUCCEEDED.
PASS: S3 artifact exists (...)
PASS: worker drained and exited.
===== cloud-smoke completed =====
```

The smoke workspace, collection, campaign, document, and artifact remain as operational audit evidence.

### List tagged AWS resources

```bash
make cloud-inventory
```

This lists resources tagged:

```text
Project=openrevive
Environment=demo
```

It is useful for checking that the account contains only expected demo resources.

Historical ECS task-definition revisions and inactive ECS services may appear in AWS inventory. They do not run or incur Fargate compute charges when their service has:

```text
desired = 0
running = 0
status = INACTIVE
```

### View current-month costs

```bash
make cloud-costs
```

This queries Cost Explorer grouped by AWS service.

Cost Explorer is delayed. It is not a real-time billing safeguard and can show tiny or incomplete amounts shortly after deployment.

The immediate protections are:

- AWS Budget notifications.
- Aurora capacity limits.
- One API task.
- Finite crawler workers.
- S3 artifact lifecycle expiry.
- ECR image lifecycle retention.
- `make cloud-kill`.
- `make cloud-down`.
- `make cloud-nuke`.

## Teardown

### Remove runtime compute only

```bash
make cloud-down
```

This first applies the kill behavior, then destroys runtime resources.

It removes:

```text
ECS cluster
ECS API service
ECS task definitions
Application Load Balancer
EventBridge Pipe
runtime CloudWatch log groups
runtime IAM resources
```

It retains:

```text
Aurora
S3 artifacts
ECR repository/images
SQS queues
foundation IAM roles
VPC and subnets
AWS Budget
```

Use this when you want to stop the main compute costs but preserve database and artifact data for a later redeploy.

### Destroy all demo resources and data

```bash
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

This is intentionally guarded.

It destroys the runtime layer, empties the artifact bucket, then removes foundation resources including Aurora, S3, ECR, SQS, secrets, VPC networking, and budget configuration.

Run it only when all demo data can be discarded.

## Common operational failures

### `cloud-check` reports API desired/running count is zero

The environment was probably stopped.

```bash
make cloud-resume
make cloud-status
```

### API health check fails after deployment

Inspect API logs:

```bash
make cloud-logs COMPONENT=api
```

Common causes:

- ECS task startup failure.
- Database credential or database connectivity failure.
- Migration failure.
- Incorrect container image.
- ALB target health failure.

### Campaign remains `RUNNING`

Check the worker path:

```bash
make cloud-status
make cloud-logs COMPONENT=worker
```

Look for:

- Pipe not `RUNNING`.
- SQS message backlog.
- Worker task startup failure.
- Network fetch failure.
- Job retries or terminal failures.

Then inspect the campaign page for job-level failure details.

### Worker starts but exits without processing work

This usually means no eligible job existed when the worker claimed work.

Possible causes:

- Campaign was not actually transitioned to `RUNNING`.
- Another worker already holds a valid lease.
- The worker is handling a duplicate wake-up after work completed.
- All jobs are terminal.

This is expected for duplicate event delivery after a completed campaign.

### SQS queue has messages but no worker launches

Check Pipe state:

```bash
make cloud-status
```

Then inspect the Pipe directly:

```bash
AWS_PROFILE=openrevive \
AWS_REGION=ap-south-1 \
AWS_PAGER="" \
aws pipes describe-pipe \
  --name openrevive-demo-crawl-wakeup \
  --output json
```

### Vercel UI works but API requests fail

Verify the production proxy:

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
```

The frontend uses a Vercel rewrite:

```text
/api/*
  -> AWS ALB origin
```

The browser should call the Vercel origin rather than the HTTP-only ALB directly.

## Operating recommendation

For normal demonstrations:

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
```

After the demonstration:

```bash
make cloud-kill
```

When the environment is no longer needed:

```bash
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

## Private access

OpenRevive production access is protected with HTTP Basic Authentication.

The same credentials protect:

    Vercel frontend routes
    Vercel /api/* proxy routes
    FastAPI application endpoints

The ALB health endpoints remain unauthenticated because AWS uses them for ECS target health checks:

    /health
    /health/ready

### Retrieve credentials

The deployment bootstrap creates credentials once and stores them locally:

    infra/.local/basic-auth.json

This directory is Git-ignored and must never be committed.

To display the credentials on the machine that deployed OpenRevive:

    python3 - <<'PY2'
    import json
    from pathlib import Path

    payload = json.loads(
        Path("infra/.local/basic-auth.json").read_text(encoding="utf-8")
    )

    print(f"Username: {payload['username']}")
    print(f"Password: {payload['password']}")
    PY2

Store the password in a password manager. Do not paste it into tickets, commit it, or add it to documentation.

### Browser access

Open the Vercel deployment URL. Your browser shows an HTTP Basic Auth prompt.

Use the username and password from:

    infra/.local/basic-auth.json

### Programmatic API access

Use the HTTPS Vercel API proxy for programmatic access. Do not use the raw HTTP ALB URL.

    curl --user "username:password" \
      https://openrevive-aws-demo.vercel.app/api/v1/workspaces

The same standard HTTP Basic Auth header works for future clients, scripts, and agent integrations.

### Credential rotation

To rotate credentials:

1. Replace `infra/.local/basic-auth.json` with a new JSON object containing `username` and `password`.
2. Run `make cloud-auth-bootstrap` to update AWS Secrets Manager.
3. Update the matching Vercel Production environment variables.
4. Run `make cloud-up` so ECS starts API tasks with the updated secret.
5. Redeploy Vercel Production.
6. Run authenticated `cloud-check` and `cloud-smoke`.

The current Basic Auth setup is a private-access gate for the demo. A future public API should use HTTPS-only custom-domain infrastructure and a stronger identity model.

