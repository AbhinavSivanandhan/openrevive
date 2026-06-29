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

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "api_domain_name" {
  type = string
}

variable "route53_zone_id" {
  type = string
}

variable "database_name" {
  type    = string
  default = "openrevive"
}

variable "database_master_username" {
  type    = string
  default = "openrevive_master"
}

variable "aurora_engine_version" {
  type     = string
  default  = null
  nullable = true
}

variable "aurora_min_capacity" {
  type    = number
  default = 0.5
}

variable "aurora_max_capacity" {
  type    = number
  default = 1
}

variable "artifact_retention_days" {
  type    = number
  default = 14
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

variable "budget_alert_email" {
  type    = string
  default = ""
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "allow_data_destroy" {
  type    = bool
  default = false
}
