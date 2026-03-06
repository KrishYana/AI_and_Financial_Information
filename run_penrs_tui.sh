#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! python3 -c "import textual" >/dev/null 2>&1; then
  echo "Missing dependency: textual" >&2
  echo "Install with: python3 -m pip install --user --break-system-packages textual" >&2
  exit 1
fi

exec python3 penrs_tui.py
