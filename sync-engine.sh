#!/usr/bin/env bash
# sync-engine.sh — pull the latest audit templates from arkhitek_neo-coreteks.
#
# The engine/audits/ directory mirrors 00-core/engine/audits/ in the main
# arkhitek_neo-coreteks repo. Run this whenever those templates are updated.
#
# Usage:
#   ./sync-engine.sh
#   ./sync-engine.sh --source ~/path/to/arkhitek_neo-coreteks
#   ./sync-engine.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/engine/audits"

# Default source path
SOURCE_DEFAULT="$HOME/arkhitek_neo-coreteks/00-core/engine/audits"
SOURCE="$SOURCE_DEFAULT"
DRY_RUN=false

# Parse flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)   SOURCE="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--source PATH] [--dry-run]"
      echo "  --source PATH   Path to arkhitek_neo-coreteks/00-core/engine/audits"
      echo "  --dry-run       Show what would be copied without copying"
      exit 0 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -d "$SOURCE" ]]; then
  echo "Source not found: $SOURCE"
  echo "Set --source or clone arkhitek_neo-coreteks to $HOME/arkhitek_neo-coreteks"
  exit 1
fi

echo "Syncing audit templates:"
echo "  from: $SOURCE"
echo "  to:   $DEST"
echo ""

AUDIT_FILES=(
  "baseline-site-audit.md"
  "seo-audit.md"
  "content-audit.md"
  "conversion-audit.md"
  "web-performance-audit.md"
  "geo-ai-visibility-audit.md"
  "schema-audit.md"
  "brand-data-completeness-audit.md"
)

for file in "${AUDIT_FILES[@]}"; do
  src="$SOURCE/$file"
  dst="$DEST/$file"
  if [[ ! -f "$src" ]]; then
    echo "  SKIP  $file (not found in source)"
    continue
  fi
  if [[ -f "$dst" ]] && diff -q "$src" "$dst" > /dev/null 2>&1; then
    echo "  SAME  $file"
  elif [[ "$DRY_RUN" == true ]]; then
    echo "  WOULD UPDATE  $file"
  else
    cp "$src" "$dst"
    echo "  UPDATED  $file"
  fi
done

if [[ "$DRY_RUN" == false ]]; then
  echo ""
  echo "Done. Commit the changes if anything was updated:"
  echo "  cd $SCRIPT_DIR && git add engine/audits/ && git commit -m 'Sync audit templates from neo-coreteks'"
fi
