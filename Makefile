SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: setup infra-up infra-down infra-reset infra-logs verify

setup:
	./scripts/bootstrap-macos.sh
	$(MAKE) infra-up

infra-up:
	$(COMPOSE) up -d postgres redis minio
	$(COMPOSE) run --rm minio-init
	$(COMPOSE) ps

infra-down:
	$(COMPOSE) down

infra-reset:
	$(COMPOSE) down -v --remove-orphans

infra-logs:
	$(COMPOSE) logs -f --tail=200

verify:
	./scripts/verify-dev-env.sh
