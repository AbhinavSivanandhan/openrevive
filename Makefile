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
.PHONY: bootstrap up vercel-up aws-stop aws-down aws-nuke status orphans seed-demo

bootstrap:
	./infra/scripts/bootstrap.sh

up:
	./infra/scripts/aws-up.sh
	./infra/scripts/vercel-up.sh
	@if [ "$(DEMO)" = "python-docs" ]; then \
		./infra/scripts/seed-demo.sh; \
	elif [ -n "$(DEMO)" ] && [ "$(DEMO)" != "none" ]; then \
		echo "Unknown DEMO value: $(DEMO). Use none or python-docs."; \
		exit 2; \
	fi

vercel-up:
	./infra/scripts/vercel-up.sh

aws-stop:
	./infra/scripts/aws-stop.sh

aws-down:
	./infra/scripts/aws-down.sh

aws-nuke:
	CONFIRM=$(CONFIRM) ./infra/scripts/aws-nuke.sh

status:
	./infra/scripts/aws-status.sh

orphans:
	./infra/scripts/aws-orphans.sh

seed-demo:
	./infra/scripts/seed-demo.sh
