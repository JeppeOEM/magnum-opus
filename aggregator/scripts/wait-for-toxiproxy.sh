#!/bin/sh
# Usage: wait-for-toxiproxy.sh [max_seconds]
# Polls the toxiproxy API until it responds or the timeout elapses.
# Exit 0 = ready; exit 1 = timeout.
set -e

TOXIPROXY_API="${TOXIPROXY_API:-http://localhost:8474}"
MAX_WAIT="${1:-30}"

i=0
while [ "$i" -lt "$MAX_WAIT" ]; do
    if curl -sf "${TOXIPROXY_API}/proxies" >/dev/null 2>&1; then
        echo "toxiproxy ready at ${TOXIPROXY_API}"
        exit 0
    fi
    sleep 1
    i=$((i + 1))
done

echo "TIMEOUT: toxiproxy not ready at ${TOXIPROXY_API} after ${MAX_WAIT}s" >&2
exit 1
