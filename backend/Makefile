.DEFAULT_GOAL := help
UV := uv run --frozen

.PHONY: help
help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

.PHONY: sync
sync: ## Install the locked dependencies, dev tools included, into .venv
	uv sync --frozen

.PHONY: run
run: ## Start `app serve` with MODE=dev on this machine
	MODE=dev $(UV) app serve

.PHONY: fmt
fmt: ## Format the code and apply safe lint fixes
	$(UV) ruff format .
	$(UV) ruff check --fix .

.PHONY: fmt-check
fmt-check:
	$(UV) ruff format --check .

.PHONY: lint
lint: ## ruff (ruff.toml) and the architecture contracts (.importlinter)
	$(UV) ruff check .
	$(UV) lint-imports --no-cache

.PHONY: typecheck
typecheck: ## pyright in strict mode (pyrightconfig.json)
	$(UV) pyright

# Called by name, so they look unused: FastAPI route decorators, pytest fixtures, hooks and pytestmark, http.server's
# do_<METHOD> handlers, each CLI command module's run(), which app.main.cli imports when its command runs, and what
# Alembic reads from a revision file; tests/vulture_whitelist.py names the attributes openapi-core reads through its
# protocols. models.py is left out: a column is part of the schema (tests/test_schema_db.py
# compares it with the migrations) whether or not code reads it.
.PHONY: deadcode
deadcode:
	$(UV) vulture src tests tests/vulture_whitelist.py --min-confidence 60 --exclude '*/models.py' \
		--ignore-decorators '@*.get,@*.post,@*.put,@*.patch,@*.delete,@pytest.fixture' \
		--ignore-names 'do_GET,run,pytest_*,pytestmark,revision,down_revision,upgrade'

.PHONY: test
test: ## pytest without the db tests; writes test-report.xml for scripts/check_locks.py
	$(UV) pytest -m "not db" --junitxml=test-report.xml

# Not part of check: these start PostgreSQL containers, which run only in CI (AGENTS.md, Verification).
.PHONY: test-db
test-db: ## Needs Docker: the db tests; writes test-db-report.xml for scripts/check_locks.py
	$(UV) pytest -m db --junitxml=test-db-report.xml

.PHONY: migration
migration: ## Needs Docker: generate a revision from the models, make migration MSG=add_widgets
	@test -n "$(MSG)" || { echo "usage: make migration MSG=<name>"; exit 1; }
	$(UV) python tests/generate_migration.py "$(MSG)"
	$(UV) ruff format src/app/main/migrations/versions
	$(UV) ruff check --fix src/app/main/migrations/versions

# squawk is pinned in tools/package.json (Dependabot reads it). --ignore-scripts: squawk-cli ships its binary as a
# platform package and runs no install script.
SQUAWK := tools/node_modules/.bin/squawk
REVISION_SQL := .revision-sql

$(SQUAWK): tools/package-lock.json
	npm ci --prefix tools --ignore-scripts --no-audit --no-fund
	@touch $@

.PHONY: squawk
squawk: $(SQUAWK) ## Lock and compatibility checks on every revision's SQL (.squawk.toml; needs Node, no database)
	@rm -rf $(REVISION_SQL)
	$(UV) python tests/revision_sql.py $(REVISION_SQL)
	$(SQUAWK) --config .squawk.toml $(REVISION_SQL)/*.sql

.PHONY: migrations-immutable
migrations-immutable: ## Revisions already on BASE (default origin/main) must not change
	python3 ../scripts/check_migrations_immutable.py $(BASE)

.PHONY: lock-check
lock-check: ## uv.lock matches pyproject.toml
	uv lock --check

BASE ?= origin/main

.PHONY: check
check: lock-check fmt-check lint typecheck deadcode test squawk migrations-immutable ## CI's backend job, minus test-db
