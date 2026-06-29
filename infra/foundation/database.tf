resource "aws_rds_cluster" "main" {
  cluster_identifier = "${local.name}-aurora"

  engine         = "aurora-postgresql"
  engine_mode    = "provisioned"
  engine_version = var.aurora_engine_version

  database_name   = var.database_name
  master_username = var.database_master_username

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]

  storage_encrypted       = true
  backup_retention_period = 1
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = var.allow_data_destroy

  final_snapshot_identifier = (
    var.allow_data_destroy
    ? null
    : "${local.name}-final"
  )

  copy_tags_to_snapshot = true

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_capacity
    max_capacity = var.aurora_max_capacity
  }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name}-aurora-writer"
  cluster_identifier = aws_rds_cluster.main.id

  engine         = aws_rds_cluster.main.engine
  engine_version = aws_rds_cluster.main.engine_version
  instance_class = "db.serverless"

  publicly_accessible = false
}
