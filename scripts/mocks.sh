#!/usr/bin/env bash
# Start / stop the two mock tenants (variant a on :8401, variant b on :8402).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p var
case "${1:-start}" in
  start)
    for spec in "8401 a" "8402 b"; do
      set -- $spec
      if curl -fs "http://127.0.0.1:$1/_admin/faults" >/dev/null 2>&1; then continue; fi
      MOCK_VARIANT=$2 nohup python -m uvicorn mock_bank.app:app --host 127.0.0.1 --port "$1" \
        --log-level warning > "var/mock-$1.log" 2>&1 < /dev/null &
      echo $! > "var/mock-$1.pid"
    done
    for p in 8401 8402; do
      for _ in $(seq 1 50); do curl -fs "http://127.0.0.1:$p/_admin/faults" >/dev/null 2>&1 && break; sleep 0.2; done
      echo "mock tenant on :$p -> $(curl -fs http://127.0.0.1:$p/_admin/faults)"
    done ;;
  stop)
    for p in 8401 8402; do [ -f "var/mock-$p.pid" ] && kill "$(cat var/mock-$p.pid)" 2>/dev/null || true; rm -f "var/mock-$p.pid"; done ;;
esac
