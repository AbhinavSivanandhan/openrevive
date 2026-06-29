output "vpc_id" {
  value = aws_vpc.main.id
}

output "public_subnet_ids" {
  value = [for subnet in aws_subnet.public : subnet.id]
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

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "artifact_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
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

output "basic_auth_secret_arn" {
  value = aws_secretsmanager_secret.basic_auth.arn
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "ecs_task_role_arn" {
  value = aws_iam_role.ecs_task.arn
}

output "api_certificate_arn" {
  value = aws_acm_certificate_validation.api.certificate_arn
}
