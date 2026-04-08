.DEFAULT_GOAL := help

.PHONY: install dev test test-verbose lint format typecheck clean build docker docker-run run chat serve help

VENV := .venv/bin

install: ## Install production dependencies
	$(VENV)/pip install -e .

dev: ## Install all dependencies including dev tools
	$(VENV)/pip install -e . && $(VENV)/pip install pytest pytest-asyncio ruff

test: ## Run tests (quiet output)
	$(VENV)/pytest tests/ -q

test-verbose: ## Run tests (verbose output)
	$(VENV)/pytest tests/ -v

lint: ## Check code style with ruff
	$(VENV)/ruff check src/ tests/

format: ## Auto-format code with ruff
	$(VENV)/ruff format src/ tests/

typecheck: ## Run type checking with pyright
	$(VENV)/pyright src/

clean: ## Remove build artifacts and caches
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

build: ## Build distribution packages
	$(VENV)/pip install build && $(VENV)/python -m build

docker: ## Build Docker image
	docker build -t phynai-agent .

docker-run: ## Run Docker container with .env file
	docker run --env-file .env -p 8080:8080 phynai-agent

run: ## Run a single prompt: make run PROMPT="your prompt"
	$(VENV)/phynai run "$(PROMPT)"

chat: ## Start interactive chat session
	$(VENV)/phynai chat

serve: ## Start the HTTP server
	$(VENV)/phynai serve

help: ## Show this help message
	@echo "phynai-agent — developer targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
