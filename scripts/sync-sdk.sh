#!/usr/bin/env bash
# Refresh the vendored SDK copies from the installed Vee.
#
# vee.py/vee.ts must sit beside the plugins that import them — both here (one
# copy per category folder) and at runtime (one copy in the plugins folder).
# Vee's PluginDiscovery skips them by name, so they are never run as plugins.
set -euo pipefail
cd "$(dirname "$0")/.."
VEE="${VEE:-vee}"
command -v "$VEE" >/dev/null || { echo "vee not on PATH; set VEE=/path/to/vee" >&2; exit 1; }

for dir in System Developer Network Monitoring Productivity Showcase demo; do
  [ -d "$dir" ] || continue
  # A folder needs the Python SDK only if something in it imports vee.
  if grep -qlr '^from vee import\|^import vee' "$dir" 2>/dev/null; then
    "$VEE" sdk py --out "$dir" >/dev/null && echo "  $dir/vee.py"
  fi
  if grep -qlr 'from "\./vee\.ts"' "$dir" 2>/dev/null; then
    "$VEE" sdk ts --out "$dir" >/dev/null && echo "  $dir/vee.ts"
  fi
done
echo "SDK copies refreshed from $("$VEE" --version)"
