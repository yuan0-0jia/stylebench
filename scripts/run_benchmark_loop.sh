#!/bin/bash
# Run benchmark in a loop, waiting after rate limits.
# Usage: ./scripts/run_benchmark_loop.sh [--wait-hours 4] [extra args for run_benchmark.py]
#
# The script runs run_benchmark.py, and if it exits (rate limit or completion),
# checks if there's still pending work. If so, waits and retries.
# Stops when all trials are complete or after 10 consecutive failures.

set -euo pipefail

WAIT_HOURS=4
EXTRA_ARGS=()

# Parse our args vs passthrough args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wait-hours)
            WAIT_HOURS="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

WAIT_SECONDS=$((WAIT_HOURS * 3600))
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MAX_RETRIES=10
retry=0

echo "=== StyleBench Benchmark Loop ==="
echo "Wait between rate limits: ${WAIT_HOURS}h"
echo "Extra args: ${EXTRA_ARGS[*]:-none}"
echo "Started at: $(date)"
echo ""

while true; do
    echo "--- Run starting at $(date) ---"

    # Run the benchmark (--yes to skip rate-limit confirmation prompts)
    set +e
    python "$SCRIPT_DIR/run_benchmark.py" --yes "${EXTRA_ARGS[@]}"
    exit_code=$?
    set -e

    echo ""
    echo "Run finished at $(date) with exit code $exit_code"

    # Check if benchmark is complete by looking at the output
    # run_benchmark.py prints "All X batches complete!" when done
    # We re-run it to check — if it says "all complete", we're done
    set +e
    completion_check=$(python "$SCRIPT_DIR/run_benchmark.py" --yes "${EXTRA_ARGS[@]}" 2>&1)
    check_exit=$?
    set -e

    if echo "$completion_check" | grep -q "All .* batches complete"; then
        echo ""
        echo "=== Benchmark complete! ==="
        echo "Finished at: $(date)"
        exit 0
    fi

    retry=$((retry + 1))
    if [ $retry -ge $MAX_RETRIES ]; then
        echo ""
        echo "=== Max retries ($MAX_RETRIES) reached. Stopping. ==="
        exit 1
    fi

    echo "Pending work remains. Waiting ${WAIT_HOURS} hours before retry ($retry/$MAX_RETRIES)..."
    echo "Will resume at: $(date -v+${WAIT_HOURS}H 2>/dev/null || date -d "+${WAIT_HOURS} hours" 2>/dev/null || echo "~${WAIT_HOURS}h from now")"
    sleep "$WAIT_SECONDS"
done
