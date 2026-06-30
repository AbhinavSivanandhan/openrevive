locals {
  name = "${var.project_name}-${var.environment}"

  shared_environment = [
    {
      name  = "AWS_REGION"
      value = var.aws_region
    },
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
      name  = "S3_BUCKET"
      value = var.artifacts_bucket_name
    },
    {
      name  = "S3_REGION_NAME"
      value = var.aws_region
    },
    {
      name  = "CRAWL_EVENT_QUEUE_URL"
      value = var.crawl_event_queue_url
    },
    {
      name  = "LOG_LEVEL"
      value = "INFO"
    }
  ]
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/openrevive/${var.environment}/api"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/openrevive/${var.environment}/worker"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "migration" {
  name              = "/openrevive/${var.environment}/migration"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_lb" "api" {
  name               = "${local.name}-api"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_security_group_id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = false
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
    matcher             = "200-399"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
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
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = var.image_uri
      essential = true

      portMappings = [{
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
      }]

      environment = concat(local.shared_environment, [
        {
          name  = "BASIC_AUTH_ENABLED"
          value = "true"
        }
      ])

      secrets = [
        {
          name      = "BASIC_AUTH_USERNAME"
          valueFrom = "${data.aws_secretsmanager_secret.basic_auth.arn}:username::"
        },
        {
          name      = "BASIC_AUTH_PASSWORD"
          valueFrom = "${data.aws_secretsmanager_secret.basic_auth.arn}:password::"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = var.image_uri
      essential = true

      command = [
        "uv",
        "run",
        "--frozen",
        "python",
        "-m",
        "app.crawler.worker_main"
      ]

      environment = concat(local.shared_environment, [
        {
          name  = "WORKER_EXIT_WHEN_IDLE"
          value = "true"
        },
        {
          name  = "WORKER_IDLE_POLLS_BEFORE_EXIT"
          value = "2"
        },
        {
          name  = "WORKER_IDLE_POLL_SECONDS"
          value = "1"
        }
      ])

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"

  execution_role_arn = var.ecs_execution_role_arn
  task_role_arn      = var.ecs_task_role_arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = "migration"
      image     = var.image_uri
      essential = true

      command = [
        "uv",
        "run",
        "--frozen",
        "alembic",
        "upgrade",
        "head"
      ]

      environment = local.shared_environment

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.migration.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "migration"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 200
  wait_for_steady_state              = false

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [var.api_task_security_group_id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

data "aws_iam_policy_document" "pipe_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["pipes.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "pipe" {
  name               = "${local.name}-crawl-pipe"
  assume_role_policy = data.aws_iam_policy_document.pipe_assume_role.json
}

resource "aws_iam_role_policy" "pipe" {
  name = "${local.name}-crawl-pipe"
  role = aws_iam_role.pipe.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadQueue"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [var.crawl_event_queue_arn]
      },
      {
        Sid      = "RunWorker"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.worker.arn]
        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      {
        Sid    = "PassWorkerRoles"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          var.ecs_execution_role_arn,
          var.ecs_task_role_arn
        ]
      }
    ]
  })
}

resource "aws_pipes_pipe" "crawl_wakeup" {
  name     = "${local.name}-crawl-wakeup"
  role_arn = aws_iam_role.pipe.arn

  source = var.crawl_event_queue_arn
  target = aws_ecs_cluster.main.arn

  source_parameters {
    sqs_queue_parameters {
      batch_size                         = 1
      maximum_batching_window_in_seconds = 0
    }
  }

  target_parameters {
    ecs_task_parameters {
      task_definition_arn = aws_ecs_task_definition.worker.arn
      task_count          = 1
      launch_type         = "FARGATE"

      network_configuration {
        aws_vpc_configuration {
          subnets          = var.public_subnet_ids
          security_groups  = [var.worker_task_security_group_id]
          assign_public_ip = "ENABLED"
        }
      }
    }
  }

  depends_on = [aws_iam_role_policy.pipe]
}

data "aws_secretsmanager_secret" "basic_auth" {
  name = "${local.name}-basic-auth"
}
