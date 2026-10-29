#!/bin/bash
# Pack everything git doesn't carry (DB, photos, settings/secrets, CSVs, Claude
# memory) into one .tar for moving the app to another Mac.
#   scripts/export_bundle.sh [output_dir]      (default: ~/Desktop)
# The tar holds settings.json (API keys + eBay tokens): move it by drive or
# AirDrop, and delete it once the new machine is running.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$HOME/Desktop}"
OUT="$OUT_DIR/ebay-lister-bundle-$(date +%Y%m%d-%H%M).tar"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cd "$REPO"

if lsof -nP -iTCP:8090 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Note: the app is running on :8090. The DB snapshot below is still consistent,"
  echo "      but stop the app first if you don't want changes made after this point lost."
fi

# Consistent snapshot of the WAL-mode DB (a plain copy can miss the -wal file).
sqlite3 products.db ".backup '$STAGE/products.db'"
# Make the snapshot a single self-contained file; the app turns WAL back on at startup.
sqlite3 "$STAGE/products.db" "PRAGMA journal_mode=DELETE;" >/dev/null
rm -f "$STAGE/products.db-wal" "$STAGE/products.db-shm"
echo "DB snapshot: $(sqlite3 "$STAGE/products.db" 'select count(*) from products') products, $(sqlite3 "$STAGE/products.db" 'select count(*) from product_images') images"
rm -f "$STAGE/products.db-wal" "$STAGE/products.db-shm"

# Files that live outside the repo go under _home/ and are put back by install_macmini.sh.
ENCODED_PROJECT="$(printf '%s' "$REPO" | sed 's/[^A-Za-z0-9]/-/g')"
MEMORY_DIR="$HOME/.claude/projects/$ENCODED_PROJECT/memory"
mkdir -p "$STAGE/_home"
[ -d "$MEMORY_DIR" ] && cp -R "$MEMORY_DIR" "$STAGE/_home/claude-memory"
[ -f "$HOME/.ebay_mobile_auth.json" ] && cp "$HOME/.ebay_mobile_auth.json" "$STAGE/_home/"

ITEMS=()
for p in settings.json CLAUDE.local.md .claude/settings.local.json photos "new ebay photos" output incoming _legacy; do
  [ -e "$p" ] && ITEMS+=("$p")
done

# No compression: the bulk is JPEGs, which don't shrink.
tar -cf "$OUT" --exclude .DS_Store --exclude __pycache__ -C "$STAGE" . -C "$REPO" "${ITEMS[@]}"

echo "Bundle: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Left out on purpose: credentials.json / token.json (legacy Google Drive only), products.db.bak_*."
