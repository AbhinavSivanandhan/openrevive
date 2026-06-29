variable "aws_region" {
  type = string
}

variable "project_name" {
  type    = string
  default = "openrevive"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "route53_zone_id" {
  type = string
}

variable "api_domain_name" {
  type = string
}

variable "api_certificate_arn" {
  type = string
}

variable "alb_security_group_id" {
  type = string
}

variable "api_task_security_group_id" {
  type = string
}

variable "worker_task_security_group_id" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "image_tag" {
  type = string
}

variable "artifact_bucket_name" {
  type = string
}

variable "database_secret_arn" {
  type = string
}

variable "database_host" {
  type = string
}

variable "database_name" {
  type = string
}

variable "basic_auth_secret_arn" {
  type = string
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "ecs_task_role_arn" {
  type = string
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "auto_stop_at_utc" {
  type    = string
  default = ""
}
