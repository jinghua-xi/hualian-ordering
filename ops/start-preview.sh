#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

cd "$(dirname -- "$(realpath -- "$0")")/.."
mkdir -p logs
exec 9>logs/startup.lock
flock -n 9 || exit 0

export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_CONTEXT_LENGTH=4096
export OLLAMA_MODELS=/root/.ollama/models
model_url=http://127.0.0.1:11434/api/tags
site_url=http://127.0.0.1:8767/

wait_for_http() {
    local url=$1 attempt
    for ((attempt = 0; attempt < 25; attempt++)); do
        if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

echo "$(date -Is) Starting ordering preview"
if ! curl -fsS --max-time 1 "$model_url" >/dev/null 2>&1; then
    nohup /usr/local/bin/ollama serve </dev/null >>logs/ollama.log 2>&1 9>&- &
fi
wait_for_http "$model_url" || { echo "Ollama did not start; see logs/ollama.log" >&2; exit 1; }

if ! curl -fsS --max-time 1 "$site_url" >/dev/null 2>&1; then
    source ./preview.env
    BIND=0.0.0.0 PORT=8767 DEMO_MODE=0 ORDERS_DB="$PWD/orders.sqlite3" \
        nohup /usr/local/miniconda3/envs/py312/bin/python3 -B server.py \
        </dev/null >>logs/preview.log 2>&1 9>&- &
fi
wait_for_http "$site_url" || { echo "Website did not start; see logs/preview.log" >&2; exit 1; }
echo "$(date -Is) Website and Ollama are ready"
