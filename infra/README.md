# OpenRevive AWS infrastructure

This directory contains the AWS deployment for the OpenRevive demo environment.

The infrastructure is intentionally split into two Terraform layers:

* **Foundation**: durable state and shared dependencies that can survive application teardown.
* **Runtime**: compute, ingress, logs, and worker orchestration that can be removed and recreated.

The deployment is designed for a private demo with explicit lifecycle commands and cost controls. It is not a production-ready multi-tenant platform.

## Architecture

```text
                                     Browser
                                        |
                                        | HTTPS
                                        v
                           Vercel-hosted Next.js application
                           UI, Basic Auth, /api/* proxy rewrite
                                        |
                                        | HTTP to API_INTERNAL_URL
                                        v
                         Public Application Load Balancer :80
                                        |
                                        | HTTP :8000
                                        v
                         ECS Fargate API service, desired count 1
                         campaign control plane and brief generation
                            |                  |                |
                            |                  |                |
                            v                  v                v
                 Aurora PostgreSQL       SQS wake-up queue   Bedrock
                 durable campaign        compact campaign    Nova Micro
                 state and frontier      event messages      campaign briefs
                                              |
                                              v
                                      EventBridge Pipe
                                              |
                                              v
                              finite ECS Fargate worker task
                              claim jobs, fetch pages, store evidence
                                |               |              |
                                |               |              |
                                v               v              v
                       Aurora PostgreSQL    S3 artifact     Bedrock
                       leases, jobs,        bucket          Nova Micro
                       documents, pacing    raw HTML        root-link selection
                                |
                                v
                       Approved public websites
                       bounded HTTP/HTTPS fetches
```

Vercel is not managed by Terraform in this repository. Terraform creates the AWS API endpoint; the Vercel deployment must be configured separately to proxy `/api/*` traffic to that endpoint.

## Directory layout

```text
infra/
├── foundation/       Durable AWS resources and shared IAM/security controls
├── runtime/          ECS, ALB, CloudWatch, and EventBridge Pipe resources
├── scripts/          Deployment, verification, lifecycle, and cost commands
├── .local/           Generated local-only deployment configuration and credentials
└── README.md
```

`infra/.local/`, Terraform state, Terraform provider directories, and generated runtime variables are ignored by Git.

## Terraform state model

This repository currently uses **local Terraform state**.

```text
infra/foundation/terraform.tfstate
infra/runtime/terraform.tfstate
```

There is no configured remote Terraform backend, state locking, shared workspace, or CI apply workflow.

Treat this deployment as a single-operator demo environment. Do not run Terraform from multiple machines or add parallel deployment automation without first introducing remote state storage and locking.

## Resource ownership

### Foundation layer

`infra/foundation/` owns resources intended to survive `make cloud-down`.

| Area               | Resources                                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Networking         | VPC, internet gateway, public route table, two public subnets, two database subnets, database subnet group |
| Security           | ALB, API task, worker task, and Aurora security groups                                                     |
| Database           | Aurora PostgreSQL Serverless v2 cluster and one serverless writer instance                                 |
| Credentials        | Aurora-managed database credential secret and OpenRevive Basic Auth secret                                 |
| Object storage     | Private S3 artifact bucket, server-side encryption, lifecycle expiry                                       |
| Container registry | ECR repository and image-retention policy                                                                  |
| Worker trigger     | SQS wake-up queue and dead-letter queue                                                                    |
| IAM                | ECS execution role, ECS task role, runtime permissions                                                     |
| Cost control       | Optional AWS Budget and alert thresholds                                                                   |

### Runtime layer

`infra/runtime/` owns resources removed by `make cloud-down`.

| Area                 | Resources                                                         |
| -------------------- | ----------------------------------------------------------------- |
| Compute              | ECS cluster, API service, API/worker/migration task definitions   |
| Ingress              | Public Application Load Balancer, HTTP listener, API target group |
| Worker orchestration | EventBridge Pipe from SQS to ECS worker tasks                     |
| Logging              | CloudWatch log groups for API, worker, and migration tasks        |

## Foundation resources

### Networking

The demo creates one VPC with two public subnets and two database subnets across the first two available Availability Zones.

```text
VPC CIDR: 10.42.0.0/16
```

Public subnets contain:

* the Application Load Balancer,
* API ECS tasks,
* finite worker ECS tasks,
* migration ECS tasks.

Database subnets contain:

* Aurora PostgreSQL Serverless v2.

The database subnets do not receive an internet route. Aurora has no public endpoint.

API and worker tasks receive public IP addresses. This is deliberate for the demo: workers must reach public websites and AWS service endpoints, and public task networking avoids the fixed cost of a NAT Gateway.

### Security groups

| Source                     | Destination             | Allowed traffic |
| -------------------------- | ----------------------- | --------------- |
| Internet                   | ALB                     | TCP `80`        |
| ALB security group         | API task security group | TCP `8000`      |
| API task security group    | Aurora security group   | TCP `5432`      |
| Worker task security group | Aurora security group   | TCP `5432`      |
| Internet                   | Worker task             | None            |
| Internet                   | Aurora                  | None            |

The API and worker security groups allow outbound traffic. That is necessary for:

* API access to Aurora, SQS, Secrets Manager, and Bedrock,
* worker access to Aurora, S3, Bedrock, and approved public websites.

### Aurora PostgreSQL

The database is Aurora PostgreSQL Serverless v2 with one writer instance.

Default demo settings:

| Setting                   |   Default |
| ------------------------- | --------: |
| Minimum Aurora capacity   | `0.5` ACU |
| Maximum Aurora capacity   | `1.0` ACU |
| Backup retention          |   `1` day |
| Storage encryption        |   Enabled |
| Public access             |  Disabled |
| Deletion protection       |  Disabled |
| Final snapshot on destroy |  Disabled |

Aurora manages the master password in AWS Secrets Manager. The password is not stored in Terraform variables, ECS environment variables, or repository configuration.

At runtime, ECS reads the Aurora secret through the task role. The application builds its async PostgreSQL connection string only in process memory using:

```text
DATABASE_SECRET_ARN
DATABASE_HOST
DATABASE_PORT
DATABASE_NAME
```

### S3 artifact bucket

The artifact bucket stores raw response bytes for successfully fetched pages.

Current behavior:

| Control          | Configuration          |
| ---------------- | ---------------------- |
| Public access    | Blocked                |
| Encryption       | SSE-S3 with AES-256    |
| Object retention | `14` days by default   |
| Bucket deletion  | `force_destroy = true` |
| Access           | ECS task role only     |

The artifact retention window is intentionally short because this is a demo environment. The bucket is not configured for versioning, cross-region replication, legal hold, immutable retention, or long-term archival.

### ECR

The deployment creates one ECR repository for the shared API, worker, and migration image.

Current behavior:

| Control              | Configuration                  |
| -------------------- | ------------------------------ |
| Repository name      | `<project>-<environment>-api`  |
| Image tag mutability | Mutable                        |
| Image cleanup        | Retain five most recent images |
| Repository deletion  | `force_delete = true`          |

`make cloud-up` builds one ARM64 image and tags it with the current Git short SHA.

### SQS and dead-letter queue

The API publishes compact campaign wake-up events after a campaign starts or resumes.

The queue does not contain crawl jobs. PostgreSQL remains the crawl frontier and job-lease authority.

Current queue settings:

| Queue               | Retention | Other behavior                                                            |
| ------------------- | --------: | ------------------------------------------------------------------------- |
| Crawl wake-up queue |    4 days | 300-second visibility timeout; moves messages to DLQ after three receives |
| Crawl wake-up DLQ   |   14 days | SQS-managed server-side encryption                                        |

EventBridge Pipes reads one wake-up message at a time and starts one finite ECS worker task for that message.

Duplicate messages or multiple worker starts are acceptable because workers claim crawl jobs through PostgreSQL leases.

### IAM

The deployment uses two ECS roles.

| Role               | Purpose                                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| ECS execution role | Pulls ECR images, writes container logs, and reads the Basic Auth secret injected into the API container.  |
| ECS task role      | Reads Aurora credentials, accesses the artifact bucket, invokes Bedrock, and publishes SQS wake-up events. |

The application containers do not receive static AWS access keys.

The same task role is currently reused by API, worker, and migration tasks. This keeps the demo simpler, but it is broader than strict per-task least privilege.

### Basic Auth secret

Terraform creates an AWS Secrets Manager secret for private-access credentials.

`make cloud-up` calls `infra/scripts/cloud-auth-bootstrap.sh`, which:

1. creates `infra/.local/basic-auth.json` if it does not exist,
2. generates a random password,
3. validates the credential format,
4. writes the credential JSON to AWS Secrets Manager,
5. injects the username and password into the API ECS task definition.

The local credential file has restricted permissions and is ignored by Git.

The deployed API requires Basic Auth for all routes except:

```text
/health
/health/ready
```

Those health routes remain unauthenticated because the ALB and deployment verification use them.

## Runtime resources

### Application Load Balancer

The runtime layer creates a public Application Load Balancer with:

```text
Listener: HTTP :80
Target group: HTTP :8000
Health endpoint: /health
```

The ALB does not terminate TLS. Vercel provides HTTPS for the browser-facing frontend, but Vercel currently proxies API traffic to the ALB over HTTP.

This is acceptable for a temporary private demo only. A production deployment should use a custom domain, ACM certificate, HTTPS listener, HTTP-to-HTTPS redirect, and stricter ingress controls.

### ECS task definitions

All tasks use:

```text
Launch type:       Fargate
Network mode:      awsvpc
Platform:          Linux ARM64
Container image:   one shared ECR image
```

| Task      |   CPU |    Memory | Purpose                                 |
| --------- | ----: | --------: | --------------------------------------- |
| API       | `512` | `1024 MB` | Runs FastAPI behind the ALB.            |
| Worker    | `256` |  `512 MB` | Claims and executes durable crawl jobs. |
| Migration | `512` | `1024 MB` | Runs `alembic upgrade head` and exits.  |

### API service

The API service normally runs with:

```text
desired count: 1
```

The service is intentionally limited to one replica for cost control.

The API handles:

* workspace, collection, and campaign lifecycle requests,
* campaign and frontier reads,
* document reads,
* campaign brief generation,
* SQS wake-up publication after a campaign transaction commits.

### Worker task

Workers are not an always-on ECS service.

EventBridge Pipes launches a worker task after an SQS wake-up event. The worker configuration is:

```text
WORKER_EXIT_WHEN_IDLE=true
WORKER_IDLE_POLLS_BEFORE_EXIT=2
WORKER_IDLE_POLL_SECONDS=1
```

The worker exits after two consecutive idle polls. This prevents continuous worker cost when the frontier is empty.

The worker itself decides whether it can claim work by reading PostgreSQL. It does not use the SQS event body as a crawl-job payload.

### Migration task

`make cloud-up` starts the migration task before the API service is scaled to one replica.

The migration command is:

```bash
uv run --frozen alembic upgrade head
```

The deployment fails if that finite migration task exits with a non-zero status.

### CloudWatch logs

The runtime creates separate log groups:

```text
/openrevive/<environment>/api
/openrevive/<environment>/worker
/openrevive/<environment>/migration
```

Each currently retains logs for 14 days.

## Configuration

### Local cloud configuration

The first cloud command creates this file when it does not exist:

```text
infra/.local/cloud.env
```

Default contents:

```bash
AWS_PROFILE=openrevive
AWS_REGION=ap-south-1
PROJECT_NAME=openrevive
ENVIRONMENT=demo
BUDGET_LIMIT_USD=10
BUDGET_EMAIL=
```

The scripts prompt for `BUDGET_EMAIL` when it is empty, then append the configured value to the file.

| Variable           | Purpose                                                 |
| ------------------ | ------------------------------------------------------- |
| `AWS_PROFILE`      | AWS CLI profile used by Terraform and AWS CLI commands. |
| `AWS_REGION`       | AWS deployment region. Default: `ap-south-1`.           |
| `PROJECT_NAME`     | Resource-name prefix and standard resource tag.         |
| `ENVIRONMENT`      | Environment-name suffix and standard resource tag.      |
| `BUDGET_LIMIT_USD` | Monthly AWS Budget limit. Default: `10`.                |
| `BUDGET_EMAIL`     | Required email address for budget alerts.               |

Every Terraform-managed resource receives these tags:

```text
Project=<PROJECT_NAME>
Environment=<ENVIRONMENT>
ManagedBy=terraform
```

### Generated runtime variables

`infra/scripts/common.sh` reads Terraform foundation outputs and creates:

```text
infra/runtime/runtime.auto.tfvars.json
```

That file contains generated runtime inputs such as:

* VPC and subnet IDs,
* security-group IDs,
* Aurora endpoint and secret ARN,
* ECR image URI,
* artifact bucket name,
* SQS queue URL and ARN,
* ECS role ARNs.

It is generated by deployment scripts and ignored by Git.

Do not hand-edit it. Run `make cloud-up` again after changing foundation resources or image configuration.

### Terraform defaults

The scripts expose only the cloud environment settings above. Other defaults live in `infra/foundation/variables.tf`.

| Variable                   | Default        |
| -------------------------- | -------------- |
| `vpc_cidr`                 | `10.42.0.0/16` |
| `database_name`            | `openrevive`   |
| `database_master_username` | `openrevive`   |
| `aurora_min_capacity`      | `0.5`          |
| `aurora_max_capacity`      | `1.0`          |
| `artifact_retention_days`  | `14`           |
| `budget_limit_usd`         | `10`           |

To change a value that is not exposed by `cloud.env`, update the Terraform configuration or run Terraform manually with the required variables.

## Vercel configuration

Vercel is deployed separately from AWS Terraform.

The demo uses Vercel’s generated project URL:

```text
https://openrevive-aws-demo.vercel.app/
```

No custom domain, Route 53 hosted zone, DNS record, ACM certificate, or AWS-managed frontend distribution is configured. Vercel provides the public HTTPS origin and certificate for this `vercel.app` URL. The Vercel application then proxies `/api/*` requests to the AWS Application Load Balancer through `API_INTERNAL_URL`.

The Next.js application rewrites browser requests from:

```text
/api/<path>
```

to:

```text
${API_INTERNAL_URL}/<path>
```

For the cloud deployment, configure these Vercel environment variables:

```bash
API_INTERNAL_URL=http://<ALB-DNS-NAME>
BASIC_AUTH_ENABLED=true
BASIC_AUTH_USERNAME=<value from infra/.local/basic-auth.json>
BASIC_AUTH_PASSWORD=<value from infra/.local/basic-auth.json>
```

The frontend proxy applies Basic Auth before serving application routes. The API independently applies Basic Auth, so the same credentials must be accepted by both layers.

The frontend supports a second Basic Auth credential pair:

```bash
BASIC_AUTH_USERNAME_2=<optional username>
BASIC_AUTH_PASSWORD_2=<optional password>
```

That is optional and is not populated by the AWS deployment scripts.

`make cloud-up` does not create, deploy, or configure the Vercel project.

## Bedrock requirement

OpenRevive uses Amazon Bedrock with Amazon Nova Micro for:

* root-page link selection in crawler workers,
* source-linked campaign brief generation in the API.

Before using those paths, ensure the target AWS account and region can invoke Nova Micro.

The task role currently includes Bedrock permissions for an APAC Nova Micro inference profile and several regional Nova Micro foundation-model ARNs. The inference-profile ARN in `infra/foundation/main.tf` contains a specific AWS account ID and is not parameterized.

When deploying to another AWS account or changing AWS regions, inspect and update the Bedrock IAM policy before deployment.

## Prerequisites

Cloud deployment requires:

* an AWS account and configured AWS CLI profile,
* permission to create and manage the listed AWS resources,
* Terraform `>= 1.8.0`,
* Docker with Buildx support,
* Git,
* Python 3,
* a valid Git commit, because image tags use `git rev-parse --short HEAD`,
* Bedrock access for Nova Micro if AI-assisted crawling and briefs are required,
* a separately deployed Vercel frontend for browser access.

The repository’s local environment checker is useful before deployment:

```bash
make verify
```

It reports whether common development tools are available. It does not validate all AWS permissions, Bedrock access, or Vercel configuration.

## Deployment sequence

Run:

```bash
make cloud-up
```

The command performs these steps:

1. Loads or creates `infra/.local/cloud.env`.
2. Initializes and applies the foundation Terraform layer.
3. Generates or uploads Basic Auth credentials to AWS Secrets Manager.
4. Reads foundation outputs.
5. Logs Docker into ECR.
6. Builds and pushes an ARM64 image tagged with the current Git short SHA.
7. Writes generated runtime Terraform variables.
8. Creates runtime infrastructure with the API desired count set to `0`.
9. Starts a finite ECS migration task.
10. Waits for Alembic migrations to succeed.
11. Updates runtime infrastructure with API desired count set to `1`.
12. Waits for the ECS API service to stabilize.
13. Calls the public ALB health endpoint.

The final command output includes the direct ALB API URL and SQS queue URL.

## Lifecycle commands

### Deploy

```bash
make cloud-up
```

Creates or updates both Terraform layers, builds the image, migrates the database, and starts the API service.

### Inspect status

```bash
make cloud-status
```

Shows:

* Aurora cluster identifier,
* artifact bucket,
* queue URL,
* API ECS desired/running/pending count,
* EventBridge Pipe state,
* approximate SQS visible and in-flight message counts,
* direct ALB API URL.

### Follow logs

```bash
make cloud-logs COMPONENT=api
make cloud-logs COMPONENT=worker
```

`COMPONENT` must be `api` or `worker`.

### Stop compute without deleting infrastructure

```bash
make cloud-kill
```

This command:

1. stops the EventBridge Pipe,
2. scales the API service to zero,
3. stops currently running ECS tasks.

Aurora, S3, ECR, SQS, VPC networking, IAM, CloudWatch log groups, and Terraform state remain.

`make cloud-stop` is an alias for `make cloud-kill`.

### Resume after a kill

```bash
make cloud-resume
```

This command:

1. scales the API service back to one replica,
2. starts the EventBridge Pipe.

It requires runtime infrastructure to still exist. It cannot recreate resources after `make cloud-down`.

### Remove runtime resources

```bash
make cloud-down
```

This command first applies `cloud-kill`, then destroys the runtime Terraform layer.

It removes:

* ECS cluster and service,
* ALB, listener, and target group,
* API, worker, and migration task definitions,
* EventBridge Pipe,
* CloudWatch log groups.

It retains:

* VPC and subnets,
* Aurora,
* S3 artifacts,
* ECR images,
* SQS queues,
* IAM roles,
* Secrets Manager secrets,
* budget resources.

Use this when preserving demo state but removing the recurring cost of the ALB and running ECS tasks.

### Destroy the complete demo environment

```bash
CONFIRM=DELETE_DEMO_DATA make cloud-nuke
```

This command refuses to run without the exact confirmation value.

It:

1. removes runtime infrastructure,
2. empties the artifact bucket,
3. destroys the foundation Terraform layer,
4. deletes durable demo resources and data.

This is irreversible in the current demo configuration because Aurora does not create a final snapshot and the artifact bucket allows forced deletion.

## Verification commands

### Read-only deployment check

```bash
make cloud-check
```

This verifies:

* direct ALB-to-API health,
* API ECS desired/running count,
* Aurora status,
* EventBridge Pipe state,
* SQS queue attributes,
* S3 lifecycle configuration,
* ECR image availability,
* AWS Budget existence.

To additionally verify the Vercel proxy and its Basic Auth path:

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-check
```

The frontend URL must use HTTPS.

### End-to-end smoke test

```bash
FRONTEND_URL=https://<your-vercel-domain> make cloud-smoke
```

The smoke test:

1. runs `cloud-check`,
2. creates a workspace and collection,
3. creates a one-page campaign,
4. starts the campaign through the Vercel API proxy,
5. waits for `SUCCEEDED`,
6. verifies that a persisted document exists,
7. verifies the raw S3 artifact exists and is non-empty,
8. verifies that the worker emitted a drain-and-exit log.

Default smoke-test seed:

```text
https://docs.python.org/3/library/asyncio.html
```

Override it only with a stable, publicly reachable HTML page:

```bash
FRONTEND_URL=https://<your-vercel-domain> \
SMOKE_SEED_URL=https://example.com \
make cloud-smoke
```

The smoke test deliberately retains its workspace, collection, crawl run, document, and raw artifact as audit evidence. S3 lifecycle rules remove the artifact after the configured retention period.

### Tagged resource inventory

```bash
make cloud-inventory
```

Lists AWS resources tagged with the configured `Project` and `Environment` values.

### Cost Explorer report

```bash
make cloud-costs
```

Shows current-month account-level Cost Explorer data grouped by AWS service.

This is not an OpenRevive-only cost report and does not use resource tags to isolate the project’s spend. Cost Explorer data can lag behind actual resource creation and deletion.

## Cost controls

The demo contains several deliberate cost controls.

| Control            | Current behavior                                                                           |
| ------------------ | ------------------------------------------------------------------------------------------ |
| API scale          | One API replica when active; zero after `cloud-kill`.                                      |
| Worker model       | Finite Fargate worker tasks exit after the frontier is idle.                               |
| Aurora cap         | Serverless v2 capped at `1.0` ACU by default.                                              |
| No NAT Gateway     | API and worker tasks receive public IPs to avoid NAT Gateway cost.                         |
| Artifact retention | S3 artifacts expire after 14 days by default.                                              |
| Image retention    | ECR retains five images.                                                                   |
| Log retention      | CloudWatch log groups retain 14 days.                                                      |
| Budget             | Default monthly budget is USD `10`, with email alerts at 50%, 80%, and 100%.               |
| Explicit teardown  | `cloud-kill`, `cloud-down`, and `cloud-nuke` separate stop, preserve, and delete behavior. |

AWS Budgets and Cost Explorer are delayed reporting tools. They are not immediate billing cutoffs.

## Known demo limitations

The infrastructure intentionally omits several production controls.

* The ALB serves HTTP only on port `80`.
* Vercel-to-ALB traffic currently uses HTTP.
* Basic Auth is the only private-access mechanism.
* Health endpoints are publicly reachable.
* ECS API and worker tasks run in public subnets with public IP addresses.
* There is one API replica and no multi-AZ application service strategy.
* Terraform state is local and not locked.
* No NAT Gateway, VPC endpoints, WAF, CloudFront, RDS Proxy, OpenSearch, Redis, or worker scheduler is deployed.
* The Aurora cluster has one writer and one-day backup retention.
* Aurora deletion protection is disabled and final snapshots are skipped.
* S3 and ECR use forced deletion to keep teardown simple.
* The ECS task role is shared across API, worker, and migration tasks.
* Bedrock model permissions include account-specific configuration that must be reviewed for another AWS account.
* Vercel configuration and deployment are outside Terraform.

These choices are appropriate for an inspectable demo environment with explicit teardown. They should be revisited before handling sensitive data, public traffic, multiple users, or sustained production workloads.
