output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "migration_task_definition_arn" {
  value = aws_ecs_task_definition.migration.arn
}

output "api_url" {
  value = "http://${aws_lb.api.dns_name}"
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}
