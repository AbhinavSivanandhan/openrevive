output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = values(aws_subnet.public)[*].id
}

output "alb_security_group_id" {
  value = aws_security_group.alb.id
}

output "api_task_security_group_id" {
  value = aws_security_group.api_task.id
}

output "worker_task_security_group_id" {
  value = aws_security_group.worker_task.id
}

output "database_secret_arn" {
  value = aws_rds_cluster.main.master_user_secret[0].secret_arn
}

output "database_host" {
  value = aws_rds_cluster.main.endpoint
}

output "database_name" {
  value = var.database_name
}

output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "crawl_event_queue_url" {
  value = aws_sqs_queue.crawl_events.url
}

output "crawl_event_queue_arn" {
  value = aws_sqs_queue.crawl_events.arn
}

output "crawl_event_dlq_url" {
  value = aws_sqs_queue.crawl_events_dlq.url
}

output "aurora_cluster_identifier" {
  value = aws_rds_cluster.main.cluster_identifier
}


output "basic_auth_secret_arn" {
  value = aws_secretsmanager_secret.basic_auth.arn
}
