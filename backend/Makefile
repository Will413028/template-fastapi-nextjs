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

# Called by name, so they look unused: FastAPI route decorators, pytest fixtures, http.server's do_<METHOD> handlers,
# and each CLI command module's run(), which app.main.cli imports when its command runs.
.PHONY: deadcode
deadcode:
	$(UV) vulture src tests --min-confidence 60 \
		--ignore-decorators '@*.get,@*.post,@*.put,@*.patch,@*.delete,@pytest.fixture' --ignore-names 'do_GET,run'

.PHONY: test
test: ## pytest; writes test-report.xml for scripts/check_locks.py
	$(UV) pytest --junitxml=test-report.xml

.PHONY: lock-check
lock-check: ## uv.lock matches pyproject.toml
	uv lock --check

.PHONY: check
check: lock-check fmt-check lint typecheck deadcode test ## Everything CI's backend job runs
