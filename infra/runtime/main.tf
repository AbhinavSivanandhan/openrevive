data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  name = "${var.project_name}-${var.environment}"

  common_environment = [
    {
      name  = "DATABASE_SECRET_ARN"
      value = var.database_secret_arn
    },
    {
      name  = "DATABASE_HOST"
      value = var.database_host
    },
    {
      name  = "DATABASE_PORT"
      value = "5432"
    },
    {
      name  = "DATABASE_NAME"
      value = var.database_name
    },
    {
      name  = "AWS_REGION"
      value = var.aws_region
    },
    {
      name  = "S3_BUCKET"
      value = var.artifact_bucket_name
    },
    {
      name  = "S3_REGION_NAME"
      value = var.aws_region
    },
    {
      name  = "LOG_LEVEL"
      value = "INFO"
    }
  ]

  api_container = {
    name      = "api"
    image     = "${var.ecr_repository_url}:${var.image_tag}"
    essential = true

    portMappings = [
      {
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
      }
    ]

    environment = concat(
      local.common_environment,
      [
        {
          name  = "BASIC_AUTH_ENABLED"
          value = "true"
        }
      ]
    )

    secrets = [
      {
        name      = "BASIC_AUTH_USERNAME"
        valueFrom = "${var.basic_auth_secret_arn}:username::"
      },
      {
        name      = "BASIC_AUTH_PASSWORD"
        valueFrom = "${var.basic_auth_secret_arn}:password::"
      },
      {
        name      = "BASIC_AUTH_USERNAME_2"
        valueFrom = "${var.basic_auth_secret_arn}:username_2::"
      },
      {
        name      = "BASIC_AUTH_PASSWORD_2"
        valueFrom = "${var.basic_auth_secret_arn}:password_2::"
      }
    ]

    healthCheck = {
      command = [
        "CMD-SHELL",
        "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()\""
      ]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }

  worker_container = {
    name      = "worker"
    image     = "${var.ecr_repository_url}:${var.image_tag}"
    essential = true

    command = [
      "uv",
      "run",
      "--frozen",
      "python",
      "-m",
      "app.crawler.worker_main"
    ]

    environment = concat(
      local.common_environment,
      [
        {
          name  = "WORKER_LEASE_SECONDS"
          value = "180"
        },
        {
          name  = "WORKER_IDLE_POLL_SECONDS"
          value = "1.0"
        },
        {
          name  = "WORKER_MAX_RESPONSE_BYTES"
          value = "2000000"
        }
      ]
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }

  migration_container = {
    name      = "migration"
    image     = "${var.ecr_repository_url}:${var.image_tag}"
    essential = true

    command = [
      "uv",
      "run",
      "--frozen",
      "alembic",
      "upgrade",
      "head"
    ]

    environment = local.common_environment

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.migration.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "migration"
      }
    }
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/openrevive/${var.environment}/api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/openrevive/${var.environment}/worker"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "migration" {
  name              = "/openrevive/${var.environment}/migration"
  retention_in_days = 7
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name}-cluster"
}

resource "aws_lb" "api" {
  name               = "${local.name}-api"
  internal           = false
  load_balancer_type = "application"

  security_groups = [var.alb_security_group_id]
  subnets         = var.public_subnet_ids

  drop_invalid_header_fields = true
  idle_timeout               = 60
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  network_mode = "awsvpc"

  cpu    = "256"
  memory = "512"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  container_definitions = jsonencode([
    local.api_container
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  network_mode = "awsvpc"

  cpu    = "256"
  memory = "512"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  container_definitions = jsonencode([
    local.worker_container
  ])
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  network_mode = "awsvpc"

  cpu    = "256"
  memory = "512"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  container_definitions = jsonencode([
    local.migration_container
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  platform_version = "LATEST"

  health_check_grace_period_seconds = 60

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    assign_public_ip = true
    subnets          = var.public_subnet_ids
    security_groups  = [var.api_task_security_group_id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [
    aws_lb_listener.http
  ]
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  platform_version = "LATEST"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    assign_public_ip = true
    subnets          = var.public_subnet_ids
    security_groups  = [var.worker_task_security_group_id]
  }
}

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    effect = "Allow"

    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json
}

resource "aws_iam_role_policy" "scheduler_stop_services" {
  name = "${local.name}-stop-services"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["ecs:UpdateService"]
      Resource = [
        aws_ecs_service.api.arn,
        aws_ecs_service.worker.arn
      ]
    }]
  })
}

resource "aws_scheduler_schedule" "auto_stop" {
  for_each = var.auto_stop_at_utc == "" ? {} : {
    api    = aws_ecs_service.api.name
    worker = aws_ecs_service.worker.name
  }

  name                         = "${local.name}-stop-${each.key}"
  schedule_expression          = "at(${var.auto_stop_at_utc})"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:updateService"
    role_arn = aws_iam_role.scheduler.arn

    input = jsonencode({
      cluster      = aws_ecs_cluster.main.name
      service      = each.value
      desiredCount = 0
    })
  }
}
