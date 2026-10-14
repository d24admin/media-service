#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is required. Install it and run 'make local' again." >&2
  exit 1
fi

if [ -z "${LOCALSTACK_AUTH_TOKEN:-}" ]; then
  echo "LOCALSTACK_AUTH_TOKEN is required for the LocalStack image used by this project." >&2
  echo "Export it before running 'make local'." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "Starting Docker Desktop..."
    open -a Docker >/dev/null 2>&1 || true
  fi
  echo "Waiting for the Docker daemon..."
  for attempt in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then
      break
    fi
    if [ "$attempt" = 60 ]; then
      echo "Docker Desktop is not running. Start it and run 'make local' again." >&2
      exit 1
    fi
    sleep 2
  done
fi

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_REGION="${AWS_REGION:-$AWS_DEFAULT_REGION}"
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:-http://localhost.localstack.cloud:4566}"
export AWS_PUBLIC_ENDPOINT_URL="${AWS_PUBLIC_ENDPOINT_URL:-http://localhost.localstack.cloud:4566}"
export MEDIA_BUCKET_NAME="${MEDIA_BUCKET_NAME:-media-service-local}"
export MEDIA_TABLE_NAME="${MEDIA_TABLE_NAME:-media-service-local}"
export RUN_LOCAL_API="${RUN_LOCAL_API:-false}"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

echo "Installing local Python dependencies..."
.venv/bin/pip install -q -r requirements-dev.txt

echo "Starting LocalStack..."
docker compose up -d localstack

echo "Waiting for LocalStack..."
for attempt in $(seq 1 60); do
  if curl -fsS http://localhost:4566/_localstack/health >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" = 60 ]; then
    echo "LocalStack did not become healthy." >&2
    docker compose logs localstack >&2 || true
    exit 1
  fi
  sleep 2
done

echo "Building the Lambda package..."
make build

echo "Provisioning LocalStack resources..."
bash scripts/provision-local.sh

API_ID="$(docker compose exec -T localstack awslocal apigatewayv2 get-apis --query "Items[?Name=='media-service-local'].ApiId | [0]" --output text)"
API_ENDPOINT="http://${API_ID}.execute-api.localhost.localstack.cloud:4566"

if [ "$RUN_LOCAL_API" = "true" ]; then
  echo "Starting the local FastAPI process at http://127.0.0.1:8000"
  exec .venv/bin/uvicorn main:app --app-dir src --host 127.0.0.1 --port 8000
fi

echo "LocalStack API Gateway + Lambda are ready."
echo "Open the browser demo at: ${API_ENDPOINT}/"
echo "Lambda API endpoint: ${API_ENDPOINT}"
