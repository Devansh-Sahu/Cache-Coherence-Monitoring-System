.PHONY: help dev test lint seed deploy-lambda infra-init infra-apply docker-up docker-down

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Redis Cache Staleness Monitor — Makefile"
	@echo "  ─────────────────────────────────────────"
	@echo "  make docker-up        Start Redis, Postgres, LocalStack"
	@echo "  make docker-down      Stop all containers"
	@echo "  make dev              Run FastAPI dev server (port 8000)"
	@echo "  make worker           Run staleness checker worker"
	@echo "  make dashboard        Run React dashboard (port 5173)"
	@echo "  make seed             Seed DynamoDB + Redis with example data"
	@echo "  make test             Run pytest suite"
	@echo "  make lint             Run ruff + mypy"
	@echo "  make format           Auto-format with ruff"
	@echo "  make deploy-lambda    Package + deploy Lambda to AWS/LocalStack"
	@echo "  make infra-init       terraform init"
	@echo "  make infra-apply      terraform apply (LocalStack)"
	@echo "  make setup-aws-local  Create LocalStack resources"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Docker
# ─────────────────────────────────────────────────────────────────────────────
docker-up:
	docker-compose up -d
	@echo "Waiting for services to be healthy..."
	@sleep 5
	@docker-compose ps

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
dev:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

worker:
	python -m workers.staleness_checker

dashboard:
	cd dashboard && npm run dev

# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────
seed:
	python -m scripts.seed_registry
	python -m scripts.seed_redis_keys

setup-aws-local:
	python -m scripts.setup_localstack

# ─────────────────────────────────────────────────────────────────────────────
# Testing & Quality
# ─────────────────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v --cov=api --cov=workers --cov-report=term-missing

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

lint:
	ruff check api/ workers/ rag/ lambdas/ tests/
	mypy api/ workers/

format:
	ruff format api/ workers/ rag/ lambdas/ tests/
	ruff check --fix api/ workers/ rag/ lambdas/ tests/

# ─────────────────────────────────────────────────────────────────────────────
# Lambda Deployment
# ─────────────────────────────────────────────────────────────────────────────
LAMBDA_PACKAGE_DIR = .lambda_build
LAMBDA_ZIP = staleness_alerter.zip

deploy-lambda:
	@echo "Packaging Lambda..."
	@rm -rf $(LAMBDA_PACKAGE_DIR)
	@mkdir -p $(LAMBDA_PACKAGE_DIR)
	pip install --target $(LAMBDA_PACKAGE_DIR) boto3 anthropic slack-sdk tenacity structlog
	cp lambdas/staleness_alerter.py $(LAMBDA_PACKAGE_DIR)/
	cp -r prompts $(LAMBDA_PACKAGE_DIR)/
	cd $(LAMBDA_PACKAGE_DIR) && zip -r ../$(LAMBDA_ZIP) .
	@echo "Deploying to LocalStack..."
	aws --endpoint-url=http://localhost:4566 lambda update-function-code \
		--function-name staleness-alerter \
		--zip-file fileb://$(LAMBDA_ZIP) \
		--region us-east-1 || \
	aws --endpoint-url=http://localhost:4566 lambda create-function \
		--function-name staleness-alerter \
		--runtime python3.11 \
		--role arn:aws:iam::000000000000:role/lambda-role \
		--handler staleness_alerter.handler \
		--zip-file fileb://$(LAMBDA_ZIP) \
		--region us-east-1
	@echo "Lambda deployed."

# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure
# ─────────────────────────────────────────────────────────────────────────────
infra-init:
	cd infra && terraform init

infra-plan:
	cd infra && terraform plan -var-file=localstack.tfvars

infra-apply:
	cd infra && terraform apply -var-file=localstack.tfvars -auto-approve

infra-destroy:
	cd infra && terraform destroy -var-file=localstack.tfvars -auto-approve
