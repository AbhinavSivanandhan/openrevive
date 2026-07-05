variable "aws_region" {
  type = string
}

variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "api_desired_count" {
  type    = number
  default = 1
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
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

variable "ecs_execution_role_arn" {
  type = string
}

variable "ecs_task_role_arn" {
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

variable "artifacts_bucket_name" {
  type = string
}

variable "crawl_event_queue_url" {
  type = string
}

variable "crawl_event_queue_arn" {
  type = string
}

# The secret is created by the foundation layer. Passing its ARN through the
# generated runtime variables avoids a runtime data-source read during destroy.
variable "basic_auth_secret_arn" {
  type = string
}
