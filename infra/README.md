# OpenRevive Cloud Infrastructure

This directory contains the active cloud deployment for OpenRevive.

## Target architecture

- Vercel: Next.js frontend
- API Gateway + Lambda: FastAPI control plane
- Neon PostgreSQL: campaign state, crawl frontier, leases, documents
- Amazon S3: crawl artifacts
- Amazon SQS + DLQ: crawl-run wake-up events
- EventBridge Pipes: SQS-to-Fargate task orchestration
- ECS Fargate RunTask: isolated crawler workers that drain work and exit

## Explicit non-goals for this deployment

- No Aurora
- No Application Load Balancer
- No NAT Gateway
- No always-on ECS services
- No Redis
- No Kubernetes

Terraform files will be added here after the application supports:
1. worker drain-and-exit mode, and
2. one post-commit crawl-run event published to SQS.
