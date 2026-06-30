SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: setup dev-up dev-down dev-reset dev-logs dev-ps verify

setup:
	./scripts/bootstrap-macos.sh

dev-up:
	$(COMPOSE) up --build

dev-down:
	$(COMPOSE) down

dev-reset:
	$(COMPOSE) down -v --remove-orphans

dev-logs:
	$(COMPOSE) logs -f --tail=200

dev-ps:
	$(COMPOSE) ps

verify:
	./scripts/verify-dev-env.sh

# OpenRevive AWS lifecycle
.PHONY: bootstrap seed-demo

bootstrap:
	./infra/scripts/bootstrap.sh

seed-demo:
	./infra/scripts/seed-demo.sh
