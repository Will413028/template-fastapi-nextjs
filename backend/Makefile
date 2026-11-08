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

# The Pydantic models of the contract (ADR fastapi-backend-stack D2; options in pyproject.toml [tool.datamodel-codegen]), then ruff's fixes and
# format. ruff reads the code through stdin under the target's name, so ruff.toml applies as it does to the file and
# the work tree is not touched. $(1) is where the result goes; $(1).raw and $(1).fixed are left next to it.
CONTRACT_MODELS := src/app/http/contract_models.py
define generate_contract_models
$(UV) datamodel-codegen --output $(1).raw && \
$(UV) ruff check --fix-only --quiet --stdin-filename $(CONTRACT_MODELS) < $(1).raw > $(1).fixed && \
$(UV) ruff format --quiet --stdin-filename $(CONTRACT_MODELS) < $(1).fixed > $(1)
endef

.PHONY: gen
gen: ## Regenerate src/app/http/contract_models.py from api/openapi.yaml
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	$(call generate_contract_models,"$$tmp/out.py") && cp "$$tmp/out.py" $(CONTRACT_MODELS)

.PHONY: gen-check
gen-check: ## Fail when src/app/http/contract_models.py differs from what make gen writes
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	$(call generate_contract_models,"$$tmp/out.py") || exit 1; \
	cmp -s "$$tmp/out.py" $(CONTRACT_MODELS) || { echo "$(CONTRACT_MODELS) is stale: run make gen"; exit 1; }

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
# do_<METHOD> handlers, each CLI command module's run(), which app.cli imports when its command runs, and what
# Alembic reads from a revision file; tests/vulture_whitelist.py names the attributes openapi-core reads through its
# protocols. models.py is left out: a column is part of the schema (tests/test_schema_db.py
# compares it with the migrations) whether or not code reads it. contract_models.py is left out for the same reason: it
# mirrors the contract's schemas (make gen-check), and Pydantic reads a response's fields when it serializes them.
.PHONY: deadcode
deadcode:
	$(UV) vulture src tests tests/vulture_whitelist.py --min-confidence 60 --exclude '*/models.py,*/contract_models.py' \
		--ignore-decorators '@*.get,@*.post,@*.put,@*.patch,@*.delete,@pytest.fixture' \
		--ignore-names 'do_GET,run,pytest_*,pytestmark,revision,down_revision,upgrade'

.PHONY: test
test: ## pytest without the db tests; writes test-report.xml for scripts/check_locks.py and .coverage
	$(UV) pytest -m "not db" --junitxml=test-report.xml --cov --cov-report=

# Not part of check: these start PostgreSQL containers, which run only in CI (AGENTS.md, Verification).
.PHONY: test-db
test-db: ## Needs Docker: the db tests; writes test-db-report.xml for scripts/check_locks.py, adds to .coverage
	$(UV) pytest -m db --junitxml=test-db-report.xml --cov --cov-append --cov-report=

# Reported, not gated (AGENTS.md Verification): total line coverage of the last make test (and test-db), and with
# COVER_BASE (a ref) the coverage of the lines changed since it. CI sets COVER_BASE on pull requests.
.PHONY: coverage
coverage: ## Markdown coverage report from .coverage
	@$(UV) coverage xml -q -o coverage.xml
	@printf '## Backend coverage\n\nTotal line coverage: **%s%%** (generated code excluded)\n\n' "$$($(UV) coverage report --format=total)"
	@if [ -n "$(COVER_BASE)" ]; then \
		tmp=$$(mktemp) && $(UV) diff-cover coverage.xml --compare-branch="$(COVER_BASE)" --format "markdown:$$tmp" >/dev/null \
		&& cat "$$tmp"; status=$$?; rm -f "$$tmp"; exit $$status; fi

.PHONY: migration
migration: ## Needs Docker: generate a revision from the models, make migration MSG=add_widgets
	@test -n "$(MSG)" || { echo "usage: make migration MSG=<name>"; exit 1; }
	$(UV) python tests/generate_migration.py "$(MSG)"
	$(UV) ruff format src/app/migrations/versions
	$(UV) ruff check --fix src/app/migrations/versions

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
	uv run --no-project --python 3.14 python ../scripts/check_migrations_immutable.py $(BASE)

.PHONY: lock-check
lock-check: ## uv.lock matches pyproject.toml
	uv lock --check

BASE ?= origin/main

# oasdiff compares two OpenAPI documents. It is installed from its release archive; the checksums are the release's
# checksums.txt (`scripts/tool_versions.py bump oasdiff` rewrites them). macOS has one universal archive. The version is
# in the file name so that a bump downloads the new release instead of keeping an older binary.
OASDIFF_VERSION := 1.35.0
OASDIFF := bin/oasdiff-$(OASDIFF_VERSION)
OASDIFF_SHA256_darwin_all   := 964ad2c0331768c4515fc49779827094bc89a65b1d309dac28886d1c3faf305c
OASDIFF_SHA256_linux_amd64  := a8023002fcb2eaa7f5758148a9303cb63d33b939886df312268bc0fc7b939140
OASDIFF_SHA256_linux_arm64  := 1f8b5aa033cd0240ae6c53460268bcd6e578526666a4487a781ece8cef39fbdd
OASDIFF_OS := $(shell uname -s | tr A-Z a-z)
OASDIFF_PLATFORM := $(if $(filter darwin,$(OASDIFF_OS)),darwin_all,$(OASDIFF_OS)_$(subst aarch64,arm64,$(subst x86_64,amd64,$(shell uname -m))))

.PHONY: tools
tools: $(OASDIFF) ## Download the pinned release binaries into bin/

$(OASDIFF):
	@sum="$(OASDIFF_SHA256_$(OASDIFF_PLATFORM))"; \
	if [ -z "$$sum" ]; then echo "no oasdiff checksum for $(OASDIFF_PLATFORM)"; exit 1; fi; \
	tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	archive="oasdiff_$(OASDIFF_VERSION)_$(OASDIFF_PLATFORM).tar.gz"; \
	curl -sSfL -o "$$tmp/$$archive" "https://github.com/oasdiff/oasdiff/releases/download/v$(OASDIFF_VERSION)/$$archive" && \
	(cd "$$tmp" && echo "$$sum  $$archive" | shasum -a 256 -c -) && \
	tar -xzf "$$tmp/$$archive" -C "$$tmp" oasdiff && \
	mkdir -p bin && chmod 0755 "$$tmp/oasdiff" && mv "$$tmp/oasdiff" $@

.PHONY: check
check: lock-check gen-check fmt-check lint typecheck deadcode test squawk migrations-immutable ## CI's backend job, minus test-db
