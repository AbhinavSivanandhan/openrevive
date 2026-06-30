output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "api_base_url" {
  value = "http://${aws_lb.api.dns_name}"
}

output "migration_task_definition_arn" {
  value = aws_ecs_task_definition.migration.arn
}

output "worker_task_definition_arn" {
  value = aws_ecs_task_definition.worker.arn
}

output "pipe_name" {
  value = aws_pipes_pipe.crawl_wakeup.name
}

output "api_log_group_name" {
  value = aws_cloudwatch_log_group.api.name
}

output "worker_log_group_name" {
  value = aws_cloudwatch_log_group.worker.name
}
