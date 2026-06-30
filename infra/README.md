# OpenRevive AWS demo stack

## Architecture

Vercel frontend → AWS ALB → ECS/Fargate API → Aurora PostgreSQL Serverless v2

The API publishes a crawl wake-up event to SQS after a campaign becomes
RUNNING. EventBridge Pipes launches an on-demand Fargate worker. The
worker drains PostgreSQL-backed crawl jobs and exits after idle cycles.

## Lifecycle

```bash
make cloud-up
make cloud-status
make cloud-logs
make cloud-stop
make cloud-resume
make cloud-kill
make cloud-down
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

`cloud-down` removes runtime compute and networking while retaining
Aurora, S3, ECR, SQS, IAM, and budget resources.

`cloud-nuke` destroys everything, including demo data.

## Verification and operations

Run these commands after deployment or whenever you want an operational check:

- `make cloud-check` verifies API health, ECS, Aurora, SQS, EventBridge Pipe,
  S3 lifecycle, ECR image availability, AWS Budget, and optionally the Vercel proxy.
- `make cloud-smoke` first runs `cloud-check`, then runs one bounded crawl and
  verifies final status, persisted S3 evidence, and the worker drain log.
- `make cloud-inventory` lists AWS resources tagged for this OpenRevive demo.
- `make cloud-costs` shows current-month account costs by AWS service. Billing
  data can lag, so it is informational rather than a real-time cost guard.

The smoke test retains its workspace, collection, crawl run, and artifact as
audit evidence. S3 lifecycle rules remove artifacts after the configured period.
