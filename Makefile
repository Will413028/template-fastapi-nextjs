COMPOSE = docker compose
COMPOSE_DEV = $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

# --- Stack ---

# The VM stack without the tunnel: nothing is published, so reach it with `docker compose exec`.
.PHONY: up
up:
	$(COMPOSE) up -d --wait

# Production on the VM: MODE=prod and the Cloudflare Tunnel (needs .secrets/tunnel_token from the dashboard).
.PHONY: up-prod
up-prod:
	MODE=prod $(COMPOSE) --profile tunnel up -d --wait

.PHONY: dev
dev:
	@echo "Dev stack: http://localhost:3000 (the API under /api)"
	$(COMPOSE_DEV) up --build --watch

.PHONY: down
down:
	$(COMPOSE) down

.PHONY: down-v
down-v:
	$(COMPOSE) down -v

.PHONY: build
build:
	$(COMPOSE) build

.PHONY: restart
restart:
	$(COMPOSE) restart

# --- Database ---

# Applies the revisions to the stack's database (app migrate in the migrate service). make up and make dev run it
# before the backend starts; run it by hand after generating a revision while the stack is up.
.PHONY: migration
migration:
	$(COMPOSE) build backend
	$(COMPOSE) run --rm migrate

# Generates backend/src/app/main/migrations/versions/r<timestamp>_<MSG>.py from the models (needs Docker and uv):
#   make generate-migration MSG=add_widgets
.PHONY: generate-migration
generate-migration:
	$(MAKE) -C backend migration MSG="$(MSG)"

# --- Seed ---

# Creates the admin account in the running stack (app seed-admin; an existing account is left unchanged):
#   make seed ADMIN_ACCOUNT=admin
# The password is read from the terminal without echo, or from stdin when it is piped, and reaches the container on
# its stdin (ADMIN_PASSWORD_FILE=/dev/stdin): never on a command line, in an environment variable or in .env. The account
# reaches the shell through the environment (export), never pasted into the recipe, so its characters are not shell code.
.PHONY: seed
seed: export ADMIN_ACCOUNT := $(ADMIN_ACCOUNT)
seed:
	@test -n "$$ADMIN_ACCOUNT" || { echo "usage: make seed ADMIN_ACCOUNT=<name> (the password is read from the terminal or stdin)"; exit 1; }
	@if [ -t 0 ]; then \
		trap 'stty echo' EXIT INT TERM; printf 'Password for %s: ' "$$ADMIN_ACCOUNT" >&2; stty -echo; \
		IFS= read -r pw; stty echo; echo >&2; \
	else IFS= read -r pw; fi; \
	printf '%s' "$$pw" | $(COMPOSE) exec -T -e ADMIN_ACCOUNT -e ADMIN_PASSWORD_FILE=/dev/stdin \
		backend app seed-admin

# --- Logs ---

.PHONY: logs
logs:
	$(COMPOSE) logs -f

.PHONY: logs-backend
logs-backend:
	$(COMPOSE) logs -f backend

.PHONY: logs-frontend
logs-frontend:
	$(COMPOSE_DEV) logs -f frontend

.PHONY: logs-postgres
logs-postgres:
	$(COMPOSE) logs -f postgres

# --- Status ---

.PHONY: status
status:
	$(COMPOSE) ps

.PHONY: init-env
init-env:
	@python3 scripts/init_env.py


# --- Checks ---

# hadolint is installed from its release binary; the checksums are the release's checksums.sha256.
HADOLINT_VERSION := 2.15.1
HADOLINT := bin/hadolint
HADOLINT_SHA256_macos-arm64  := 5c09f3213f8e40406abe048233d985eebef336d4a6a20021be47fadb6cf480a2
HADOLINT_SHA256_linux-x86_64 := c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507
HADOLINT_SHA256_linux-arm64  := f6198ef8090f404dbb771abfee086eb8c48ac177f30da7fd3510aca35b344b5d
HADOLINT_PLATFORM := $(subst darwin,macos,$(shell uname -s | tr A-Z a-z))-$(subst aarch64,arm64,$(shell uname -m))

# Every finding fails, infos included (hadolint's default threshold).
.PHONY: lint-docker
lint-docker: $(HADOLINT)
	$(HADOLINT) backend/Dockerfile frontend/Dockerfile

$(HADOLINT):
	@sum="$(HADOLINT_SHA256_$(HADOLINT_PLATFORM))"; \
	if [ -z "$$sum" ]; then echo "no hadolint checksum for $(HADOLINT_PLATFORM)"; exit 1; fi; \
	mkdir -p bin && \
	curl -sSfL -o $@.tmp "https://github.com/hadolint/hadolint/releases/download/v$(HADOLINT_VERSION)/hadolint-$(HADOLINT_PLATFORM)" && \
	echo "$$sum  $@.tmp" | shasum -a 256 -c - && \
	chmod 0755 $@.tmp && mv $@.tmp $@
