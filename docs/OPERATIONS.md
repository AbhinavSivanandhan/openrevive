# OpenRevive Operations Runbook

This runbook covers the deployed OpenRevive AWS demo environment: deployment, verification, normal operation, troubleshooting, credential handling, cost control, and teardown.

For the AWS resource inventory and Terraform ownership model, see [`../infra/README.md`](../infra/README.md). For crawler behavior, leases, pacing, evidence storage, and Bedrock boundaries, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 1. Operating model

```text id="ul4fqm"
Browser
  -> https://openrevive-aws-demo.vercel.app/
  -> Vercel Basic Auth + /api/* proxy
  -> public AWS Application Load Balancer
  -> ECS Fargate FastAPI service
  -> Aurora PostgreSQL Serverless v2

Campaign start or resume
  -> API commits durable campaign state in Aurora
  -> API sends SQS wake-up event
  -> EventBridge Pipe launches a finite Fargate worker
  -> worker claims PostgreSQL-backed crawl jobs
  -> worker fetches approved public pages
  -> worker stores raw bytes in S3 and evidence state in Aurora
  -> worker exits after two idle polls

Explicit campaign brief
  -> API reads persisted Aurora evidence
  -> Bedrock Nova Micro generates bounded source-linked findings
  -> API stores a cached CampaignBrief in Aurora
```

The API is the control plane. It creates and updates durable campaign state but does not fetch web pages inside request handlers.

Crawler work is durable in Aurora. SQS only wakes workers; it is not the crawl-job queue. A worker can safely start because of a duplicate wake-up event, find no eligible work, and exit without changing campaign correctness.

The current browser-facing URL is:

```text id="57ir10"
https://openrevive-aws-demo.vercel.app/
```

This is Vercel’s generated `vercel.app` URL. No custom domain, Route 53 zone, DNS record, ACM certificate, or AWS-managed frontend distribution is configured.

## 2. Before operating the environment

Cloud scripts read deployment settings from:

```text id="5osrt3"
infra/.local/cloud.env
```

The first cloud command creates it when it does not exist:

```bash id="ecxjl2"
AWS_PROFILE=openrevive
AWS_REGION=ap-south-1
PROJECT_NAME=openrevive
ENVIRONMENT=demo
BUDGET_LIMIT_USD=10
BUDGET_EMAIL=
```

The scripts prompt for `BUDGET_EMAIL` when it is empty. This value is required because the foundation deployment creates AWS Budget alerts.

Before the first deployment, verify the local environment:

```bash id="bequxu"
make verify
```

This checks common development dependencies. It does not prove that the AWS account has sufficient permissions, that Bedrock model access is enabled, or that Vercel is configured correctly.

Cloud deployment requires:

* Docker with Buildx support;
* Terraform;
* AWS CLI credentials for the profile in `infra/.local/cloud.env`;
* Python 3;
* Git, because image tags use the current Git short SHA;
* Bedrock access for Amazon Nova Micro if AI-assisted frontier selection and campaign briefs are required;
* a Vercel project configured separately from Terraform.

## 3. Normal lifecycle

### Deploy or update

```bash id="3xbz6n"
make cloud-up
```

`cloud-up` performs the complete deployment sequence:

1. loads `infra/.local/cloud.env`;
2. initializes and applies the foundation Terraform layer;
3. creates or reuses local Basic Auth credentials;
4. uploads those credentials to AWS Secrets Manager;
5. reads foundation outputs;
6. builds and pushes one ARM64 API/worker image to ECR;
7. generates `infra/runtime/runtime.auto.tfvars.json`;
8. applies runtime infrastructure with the API desired count set to `0`;
9. runs Alembic migrations as a finite ECS task;
10. fails if the migration task exits unsuccessfully;
11. updates runtime infrastructure with API desired count set to `1`;
12. waits for the API service to stabilize;
13. calls the direct ALB `/health` endpoint.

Expected final output includes:

```text id="vjlwm0"
Deployment complete.
API: http://...
Queue: https://sqs...
```

The `API` output is the raw ALB endpoint. It is useful for deployment verification, but normal browser and client traffic should use the HTTPS Vercel origin instead.

### View status

```bash id="0phk99"
make cloud-status
```

This reports:

* Aurora cluster identifier;
* S3 artifact bucket;
* SQS wake-up queue URL;
* API ECS desired, running, and pending task counts;
* EventBridge Pipe current and desired state;
* approximate SQS visible and in-flight message counts;
* direct ALB API URL.

SQS counts are approximate. A non-zero count does not necessarily mean workers are broken; it can represent a short-lived wake-up event while EventBridge Pipes is launching a task.

### Follow logs

```bash id="dbt1d1"
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
```

Use API logs for:

* request failures;
* Basic Auth failures;
* database connection errors;
* campaign state-transition problems;
* SQS wake-up publication failures;
* Bedrock campaign-brief failures.

Use worker logs for:

* job claims and lease behavior;
* domain pacing;
* fetch and parsing failures;
* S3 artifact persistence;
* root-page frontier selection;
* worker drain and exit behavior.

The standard log command supports `api` and `worker`.

To inspect migration logs after a failed deployment:

```bash id="inpq9l"
set -a
source infra/.local/cloud.env
set +a

aws logs tail \
  "$(terraform -chdir=infra/runtime output -raw migration_log_group_name)" \
  --follow \
  --since 1h
```

### Stop compute immediately

```bash id="fp2y7o"
make cloud-kill
```

`cloud-kill`:

1. stops the EventBridge Pipe, preventing new worker launches;
2. scales the API service to zero;
3. stops currently running ECS tasks in the OpenRevive cluster.

It retains Aurora, S3, ECR, SQS, VPC networking, IAM, Secrets Manager, CloudWatch logs, and Terraform state.

Use this after a demo or whenever the environment should stop consuming ECS compute.

`cloud-stop` is an alias:

```bash id="f9wchh"
make cloud-stop
```

### Resume after stopping compute

```bash id="u970jm"
make cloud-resume
```

This scales the API service back to one task and starts the EventBridge Pipe.

Use `cloud-resume` only when runtime infrastructure still exists. It cannot recreate an environment after `make cloud-down`.

After resuming, verify the environment before using it:

```bash id="3pzkto"
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check
```

### Remove runtime resources but preserve data

```bash id="wjcqlj"
make cloud-down
```

`cloud-down` first applies the kill behavior, then destroys the runtime Terraform layer.

It removes:

* ECS cluster and API service;
* API, worker, and migration task definitions;
* Application Load Balancer, listener, and target group;
* EventBridge Pipe;
* runtime CloudWatch log groups.

It retains:

* Aurora database and campaign state;
* S3 raw artifacts;
* ECR repository and images;
* SQS queue and dead-letter queue;
* IAM roles and secrets;
* VPC networking and subnets;
* budget configuration.

Use this when the demo will not be used for a while but durable evidence and campaign data should remain available for a later redeploy.

### Destroy all demo resources and data

```bash id="6zrflq"
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

This is intentionally guarded by the exact confirmation string.

`cloud-nuke`:

1. removes runtime infrastructure;
2. empties the artifact bucket;
3. destroys foundation resources;
4. deletes Aurora, S3, ECR, SQS, secrets, VPC networking, IAM resources, and budget configuration.

The current demo setup does not create a final Aurora snapshot during destruction. Treat `cloud-nuke` as irreversible.

## 4. Verification

### Read-only deployment check

```bash id="z07iu9"
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check
```

`cloud-check` does not create campaign data or change deployed resources.

It verifies:

| Check             | What it proves                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| Direct ALB health | The ALB can reach the ECS API and `/health` returns `{"status":"ok"}`.                            |
| API ECS service   | Desired and running counts are both `1`.                                                          |
| Aurora            | The cluster status is `available`.                                                                |
| EventBridge Pipe  | The SQS-to-worker pipe is `RUNNING`.                                                              |
| SQS               | The queue exists and exposes its approximate message counts.                                      |
| S3                | The artifact lifecycle rule exists.                                                               |
| ECR               | At least one deployable image exists.                                                             |
| AWS Budget        | The configured budget resource exists.                                                            |
| Vercel proxy      | When `FRONTEND_URL` is provided, the authenticated HTTPS frontend can proxy `/api/health` to AWS. |

A successful check ends with:

```text id="5p8p7r"
PASS: cloud-check completed.
```

The command confirms that the AWS Budget resource exists. Confirm the budget-notification email separately in AWS if alerts are important.

### End-to-end crawler smoke test

```bash id="cltsx9"
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-smoke
```

`cloud-smoke` runs `cloud-check` first, then creates an isolated one-page campaign through the Vercel API proxy.

The smoke campaign uses:

```text id="47rttj"
Default seed:     https://docs.python.org/3/library/asyncio.html
Maximum pages:    1
Maximum depth:    0
Request timeout:  20 seconds
Maximum attempts: 2
```

It proves this operational path:

```text id="oh3p7c"
Vercel HTTPS API proxy
  -> FastAPI
  -> Aurora campaign and crawl-job records
  -> SQS wake-up event
  -> EventBridge Pipe
  -> Fargate worker
  -> external HTML fetch
  -> S3 raw artifact
  -> Aurora CrawledDocument
  -> worker drain-and-exit log
```

A successful run ends with:

```text id="6tw3ij"
PASS: crawl reached SUCCEEDED.
PASS: S3 artifact exists (...)
PASS: worker drained and exited.
===== cloud-smoke completed =====
```

The smoke test does **not** request a campaign brief and does not verify Bedrock output quality.

It intentionally retains its workspace, collection, campaign, document, and raw artifact as audit evidence. The raw S3 artifact expires under the bucket lifecycle policy, but the Aurora records remain until deleted with a future application cleanup feature or `cloud-nuke`.

Avoid running the smoke test repeatedly without considering that it creates persistent database rows.

To use a different smoke-test target:

```bash id="zty4s7"
FRONTEND_URL=https://openrevive-aws-demo.vercel.app \
SMOKE_SEED_URL=https://example.com \
make cloud-smoke
```

Choose a stable, publicly reachable HTML page. The worker rejects unsupported content types and non-HTML assets.

### Manual AI-path verification

`cloud-smoke` validates crawl execution, not the two Bedrock workflows.

To verify the implemented AI paths manually:

1. Create a normal campaign with a meaningful research intent, a page budget greater than one, and depth greater than zero.
2. Use a root page that exposes relevant in-scope HTML links.
3. Wait for campaign completion.
4. Inspect the frontier to confirm that selected child jobs show discovery provenance and priority.
5. Use the campaign page’s explicit brief action.
6. Confirm that the saved brief contains source-linked findings that point to persisted campaign documents.

A Bedrock permission or model-access problem should leave crawl evidence intact. Link selection failure does not fail a successful root-page fetch, and a failed campaign brief remains retryable only through another explicit brief request.

### Tagged resource inventory

```bash id="l2fquj"
make cloud-inventory
```

This lists resources tagged with the configured values:

```text id="dbndba"
Project=openrevive
Environment=demo
```

Use it to identify the expected demo resources in the AWS account.

Historical ECS task-definition revisions and inactive ECS service records can appear in AWS inventory. They do not run Fargate compute when the relevant service has:

```text id="zuzlb8"
desired = 0
running = 0
status = INACTIVE
```

### Cost Explorer report

```bash id="neqazj"
make cloud-costs
```

This reports current-month AWS Cost Explorer data grouped by AWS service.

The report is account-level. It is not filtered to resources tagged for OpenRevive and should not be interpreted as an exact project-cost total.

Cost Explorer data can lag behind resource creation and deletion. A missing or small cost number immediately after deployment does not prove that no costs are accruing.

## 5. Private access and Vercel configuration

### Access model

The deployed demo uses HTTP Basic Authentication.

The same credentials should protect:

```text id="7zdjcr"
Vercel frontend routes
Vercel /api/* proxy routes
FastAPI application routes
```

The API intentionally leaves these endpoints unauthenticated:

```text id="kxaivr"
/health
/health/ready
```

They are required for ALB health checks and deployment verification.

### Local credential file

`cloud-up` creates this file once if it does not exist:

```text id="u5gigm"
infra/.local/basic-auth.json
```

Its shape is:

```json id="12v1gl"
{
  "username": "openrevive",
  "password": "<random-secret>"
}
```

The directory is Git-ignored and created with restricted local permissions. Do not commit this file, paste its password into tickets, or add it to documentation.

To view the credentials on the deployment machine:

```bash id="7fzt4y"
python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(
    Path("infra/.local/basic-auth.json").read_text(encoding="utf-8")
)

print(f"Username: {payload['username']}")
print(f"Password: {payload['password']}")
PY
```

Store the value in a password manager.

### Vercel environment variables

Vercel is deployed separately from AWS Terraform.

Set these Vercel Production environment variables:

```bash id="kj3ycv"
API_INTERNAL_URL=http://<ALB-DNS-NAME>
BASIC_AUTH_ENABLED=true
BASIC_AUTH_USERNAME=<username from infra/.local/basic-auth.json>
BASIC_AUTH_PASSWORD=<password from infra/.local/basic-auth.json>
```

The Vercel application proxies browser requests from:

```text id="ci3kxp"
/api/<path>
```

to:

```text id="cavd4l"
${API_INTERNAL_URL}/<path>
```

The frontend also supports an optional second Basic Auth pair:

```bash id="qe4cyo"
BASIC_AUTH_USERNAME_2=<optional username>
BASIC_AUTH_PASSWORD_2=<optional password>
```

The AWS deployment scripts do not create or manage those optional Vercel variables.

Use the HTTPS Vercel URL for browser access and normal programmatic API calls:

```bash id="t0muxp"
curl --user "username:password" \
  https://openrevive-aws-demo.vercel.app/api/v1/workspaces
```

Do not use the raw HTTP ALB endpoint as the standard client origin. It exists for deployment and health-check infrastructure.

### Rotate Basic Auth credentials

The Makefile does not expose a `cloud-auth-bootstrap` target. Use the script directly.

1. Replace `infra/.local/basic-auth.json` with a valid JSON object containing `username` and a password of at least 24 characters.

2. Upload the new value to AWS Secrets Manager:

   ```bash id="6xavfl"
   ./infra/scripts/cloud-auth-bootstrap.sh
   ```

3. Update matching Vercel Production environment variables.

4. Redeploy Vercel Production.

5. Restart the ECS API task so it reads the updated Secrets Manager value:

   ```bash id="kux59n"
   make cloud-kill
   make cloud-resume
   ```

6. Confirm both access layers:

   ```bash id="xvhblb"
   FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check
   ```

The current Basic Auth implementation is a private-access gate for a demo. It is not a substitute for HTTPS-only custom-domain ingress, application accounts, authorization, workspace membership, or tenant isolation.

## 6. Common operational failures

### `cloud-check` reports API desired or running count is zero

The environment is likely stopped.

```bash id="dvxuvj"
make cloud-resume
make cloud-status
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check
```

If `cloud-resume` fails because runtime Terraform state no longer exists, redeploy with:

```bash id="8nt1y1"
make cloud-up
```

### API health fails after `cloud-up`

Inspect the API service and logs:

```bash id="dnh1vv"
make cloud-status
make cloud-logs COMPONENT=api
```

Common causes:

* an ECS task could not start;
* Aurora credentials or connectivity are invalid;
* the migration task failed;
* the image was built incorrectly;
* the API is unhealthy behind the ALB;
* required runtime configuration is missing.

If the migration task failed, inspect the migration log group as shown in the log section above before changing infrastructure.

### Campaign remains `RUNNING`

First inspect campaign-level state in the browser UI. Then inspect the worker path:

```bash id="svwvi1"
make cloud-status
make cloud-logs COMPONENT=worker
```

Check for:

* EventBridge Pipe not in `RUNNING`;
* visible or in-flight wake-up messages;
* worker task startup failure;
* rejected or failed external fetches;
* jobs waiting on a valid lease;
* jobs waiting for domain pacing;
* terminal job failures that have not yet caused the campaign status to be recomputed.

A campaign may remain running briefly while a finite worker starts, claims work, or waits for a currently active domain reservation to expire.

### Worker starts and exits without processing work

This is often expected.

Possible reasons:

* the event was a duplicate wake-up after work already completed;
* another worker holds a valid PostgreSQL lease;
* the campaign is not `RUNNING`;
* all jobs are terminal;
* no job is currently eligible because of campaign state or domain pacing.

Workers do not treat the SQS event body as a crawl-job payload. They query Aurora for eligible durable work. A worker that finds none exits after two idle polls.

### SQS contains messages but no worker launches

Check the EventBridge Pipe state:

```bash id="mmpzqg"
make cloud-status
```

For direct inspection, substitute values from `infra/.local/cloud.env`:

```bash id="8cp40z"
AWS_PROFILE=openrevive \
AWS_REGION=ap-south-1 \
AWS_PAGER="" \
aws pipes describe-pipe \
  --name openrevive-demo-crawl-wakeup \
  --output json
```

Common causes include a stopped pipe, failed ECS task launch permissions, missing runtime infrastructure, or a recently stopped environment.

### Vercel UI loads but API requests fail

Verify the authenticated Vercel-to-AWS proxy:

```bash id="u54n5r"
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check
```

Check that Vercel Production has the correct values for:

```text id="wmpmjh"
API_INTERNAL_URL
BASIC_AUTH_ENABLED
BASIC_AUTH_USERNAME
BASIC_AUTH_PASSWORD
```

The expected flow is:

```text id="o8pmfq"
browser
  -> HTTPS Vercel URL
  -> Vercel /api/* proxy
  -> HTTP AWS ALB
  -> ECS API
```

The browser should not call the HTTP-only ALB directly.

### A campaign fetch fails

Inspect the campaign page and worker logs.

Expected fetch failures can include:

* external domain is outside the allowed scope;
* unsupported URL scheme;
* redirect response;
* non-HTML content type;
* response exceeds the worker byte limit;
* request timeout;
* remote TLS, DNS, or connection failure;
* retry budget exhausted.

The worker preserves structured job error state in Aurora. A failed external page does not by itself indicate an AWS infrastructure failure.

### Campaign brief fails

Inspect API logs:

```bash id="ngtbrc"
make cloud-logs COMPONENT=api
```

Likely causes:

* missing Bedrock access;
* Nova Micro model or inference-profile permissions;
* invalid model configuration;
* temporary Bedrock invocation failure;
* no usable persisted extracted text in the campaign;
* malformed or invalid model output rejected by application validation.

A brief failure does not remove crawl evidence. The user must issue another explicit brief request to retry.

## 7. Cost-control guidance

The demo includes several deliberate cost controls:

| Control          | Current behavior                                                            |
| ---------------- | --------------------------------------------------------------------------- |
| API compute      | One ECS API task while active; zero after `cloud-kill`.                     |
| Worker compute   | Finite Fargate worker tasks exit after two idle polls.                      |
| Aurora capacity  | Serverless v2 is capped at `1.0` ACU by default.                            |
| NAT avoidance    | ECS tasks receive public IPs rather than using a NAT Gateway.               |
| S3 artifacts     | Raw objects expire after 14 days by default.                                |
| ECR images       | The repository retains five images.                                         |
| CloudWatch logs  | Runtime logs retain 14 days.                                                |
| AWS Budget       | Default USD 10 budget with 50%, 80%, and 100% notifications.                |
| Fast stop        | `cloud-kill` stops API compute and worker execution.                        |
| Runtime teardown | `cloud-down` removes ALB, ECS, Pipe, and runtime logs while retaining data. |
| Full teardown    | `cloud-nuke` deletes the entire demo only with explicit confirmation.       |

AWS Budgets and Cost Explorer are delayed monitoring mechanisms. They do not stop resources automatically and are not immediate billing cutoffs.

Recommended operating pattern:

```bash id="wokn2n"
# Before a demo
make cloud-resume
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-check

# Optional end-to-end proof
FRONTEND_URL=https://openrevive-aws-demo.vercel.app make cloud-smoke

# After a demo
make cloud-kill
```

When the environment is no longer needed and data does not need to be retained:

```bash id="w563a1"
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

## 8. Operational boundaries

This deployment is intentionally a demo environment.

It does not currently provide:

* TLS termination on the AWS ALB;
* a custom DNS domain or Route 53 configuration;
* application users, sessions, workspace membership, or tenant isolation;
* WAF, CloudFront, NAT Gateway, VPC endpoints, RDS Proxy, or a worker scheduler;
* remote Terraform state, locking, shared workspaces, or CI-controlled Terraform applies;
* automated database cleanup for smoke campaigns;
* production-grade monitoring, alerting, tracing, or on-call escalation;
* automatic Bedrock health validation during `cloud-smoke`;
* long-term backups, point-in-time operational recovery procedures, or immutable artifact retention.

Do not operate Terraform from multiple machines against this local-state deployment. Introduce a remote backend and state locking before turning it into a shared or team-managed environment.
