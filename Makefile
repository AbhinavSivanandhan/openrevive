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
.PHONY: cloud-up cloud-status cloud-logs cloud-stop cloud-resume cloud-kill cloud-down cloud-nuke

cloud-up:
	./infra/scripts/cloud-up.sh

cloud-status:
	./infra/scripts/cloud-status.sh

cloud-logs:
	./infra/scripts/cloud-logs.sh $(COMPONENT)

cloud-stop:
	./infra/scripts/cloud-stop.sh

cloud-resume:
	./infra/scripts/cloud-resume.sh

cloud-kill:
	./infra/scripts/cloud-kill.sh

cloud-down:
	./infra/scripts/cloud-down.sh

cloud-nuke:
	./infra/scripts/cloud-nuke.sh
