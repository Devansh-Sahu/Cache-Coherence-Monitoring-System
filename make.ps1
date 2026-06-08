# make.ps1 — CCMS dev command runner (Windows alternative to Makefile)
# Usage: .\make.ps1 <command>
#
# Commands:
#   up           Start Docker services (Redis, Postgres, LocalStack)
#   down         Stop Docker services
#   localstack   Bootstrap LocalStack AWS resources
#   aws-setup    Create real AWS resources (needs real credentials in .env)
#   dev-api      Start FastAPI backend (hot-reload)
#   dev-worker   Start staleness checker worker
#   dev-ui       Start React dashboard
#   test         Run all tests
#   smoke        Run live Groq + Slack smoke test
#   seed         Seed sample Redis keys + registry entries
#   lint         Run ruff linter
#   logs         Tail logs from all Docker services
#   status       Show status of all services
#   help         Show this help

param([string]$cmd = "help")

$PYTHON = "C:\Users\devansh\AppData\Local\Programs\Python\Python310\python.exe"
$PYTEST  = "C:\Users\devansh\AppData\Local\Programs\Python\Python310\Scripts\pytest.exe"

switch ($cmd) {
    "up" {
        Write-Host "Starting Docker services..." -ForegroundColor Cyan
        docker-compose up -d
        Write-Host "Waiting for services to be healthy..." -ForegroundColor Yellow
        Start-Sleep 15
        docker-compose ps
    }

    "down" {
        Write-Host "Stopping Docker services..." -ForegroundColor Cyan
        docker-compose down
    }

    "localstack" {
        Write-Host "Bootstrapping LocalStack resources..." -ForegroundColor Cyan
        & $PYTHON -m scripts.setup_localstack
    }

    "aws-setup" {
        Write-Host "Setting up real AWS resources..." -ForegroundColor Cyan
        & $PYTHON scripts/aws_setup.py
    }

    "dev-api" {
        Write-Host "Starting FastAPI backend at http://localhost:8000 ..." -ForegroundColor Cyan
        & $PYTHON -m uvicorn api.main:app --reload --port 8000
    }

    "dev-worker" {
        Write-Host "Starting staleness checker worker..." -ForegroundColor Cyan
        & $PYTHON -m workers.staleness_checker
    }

    "dev-ui" {
        Write-Host "Starting React dashboard at http://localhost:5173 ..." -ForegroundColor Cyan
        Set-Location dashboard
        npm run dev
        Set-Location ..
    }

    "test" {
        Write-Host "Running all tests..." -ForegroundColor Cyan
        & $PYTEST tests/ -v --no-header --tb=short
    }

    "smoke" {
        Write-Host "Running live Groq + Slack smoke test..." -ForegroundColor Cyan
        $env:PYTHONIOENCODING = "utf-8"
        & $PYTHON scripts/smoke_test_live.py
    }

    "seed" {
        Write-Host "Seeding Redis keys and registry entries..." -ForegroundColor Cyan
        & $PYTHON -m scripts.seed_redis_keys
        & $PYTHON -m scripts.seed_registry
    }

    "lint" {
        Write-Host "Running ruff linter..." -ForegroundColor Cyan
        & $PYTHON -m ruff check api/ workers/ lambdas/ tests/
    }

    "logs" {
        Write-Host "Tailing Docker logs (Ctrl+C to stop)..." -ForegroundColor Cyan
        docker-compose logs -f
    }

    "status" {
        Write-Host "=== Docker services ===" -ForegroundColor Cyan
        docker-compose ps
        Write-Host "`n=== FastAPI health ===" -ForegroundColor Cyan
        try { (Invoke-WebRequest http://localhost:8000/health -UseBasicParsing -TimeoutSec 2).Content } catch { "  Not running" }
        Write-Host "`n=== Redis ping ===" -ForegroundColor Cyan
        try { & docker exec csm_redis redis-cli ping } catch { "  Not running" }
    }

    "help" {
        Write-Host @"
CCMS Command Runner
===================
  .\make.ps1 up           Start Docker (Redis, Postgres, LocalStack)
  .\make.ps1 down         Stop Docker
  .\make.ps1 localstack   Bootstrap LocalStack AWS resources
  .\make.ps1 aws-setup    Create real AWS resources (needs credentials)
  .\make.ps1 dev-api      Start FastAPI backend (port 8000)
  .\make.ps1 dev-worker   Start staleness checker worker
  .\make.ps1 dev-ui       Start React dashboard (port 5173)
  .\make.ps1 test         Run all 24 tests
  .\make.ps1 smoke        Live Groq + Slack smoke test
  .\make.ps1 seed         Seed sample Redis + DynamoDB data
  .\make.ps1 lint         Run ruff linter
  .\make.ps1 logs         Tail Docker logs
  .\make.ps1 status       Show service health
"@ -ForegroundColor Green
    }

    default {
        Write-Host "Unknown command: $cmd. Run .\make.ps1 help" -ForegroundColor Red
        exit 1
    }
}
