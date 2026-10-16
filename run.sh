#!/usr/bin/env bash
# Prepare a fresh checkout, then start local services. No generation is started.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo "缺少 python3。Ubuntu/Debian：sudo apt-get install python3" >&2
    exit 1
fi
exec python3 "$PROJECT_DIR/scripts/bootstrap.py" "$@"
