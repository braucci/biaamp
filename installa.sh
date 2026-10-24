#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  installa.sh — Copia BiaAmp.app in /Applicazioni e rimuove la quarantena.
#
#  Uso:   bash installa.sh                 (dopo bash build.sh)
#         bash installa.sh percorso/BiaAmp.app
#
#  © 2026 B. Raucci — Licenza MIT
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

SORGENTE="${1:-dist/BiaAmp.app}"
DESTINAZIONE="/Applications/BiaAmp.app"

if [ ! -d "$SORGENTE" ]; then
    echo "✗ Non trovo $SORGENTE — esegui prima: bash build.sh"
    exit 1
fi

if pgrep -x BiaAmp >/dev/null 2>&1; then
    echo "▸ Chiudo BiaAmp in esecuzione"
    osascript -e 'quit app "BiaAmp"' || true
    sleep 1
fi

echo "▸ Copio in /Applicazioni"
rm -rf "$DESTINAZIONE"
ditto "$SORGENTE" "$DESTINAZIONE"

echo "▸ Rimuovo l'attributo di quarantena (app non autenticata da Apple)"
xattr -dr com.apple.quarantine "$DESTINAZIONE" 2>/dev/null || true

echo "✓ Installato. Avvio…"
open "$DESTINAZIONE"
