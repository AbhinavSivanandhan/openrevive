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
