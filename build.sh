#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  build.sh — Costruisce BiaAmp.app e i pacchetti di distribuzione.
#
#  Non serve Xcode: bastano il Python di python.org e gli strumenti già
#  presenti in macOS (codesign, ditto, hdiutil).
#
#  Uso:   bash build.sh
#         PYTHON=/percorso/di/python3 bash build.sh   (altro interprete)
#
#  © 2026 B. Raucci — Licenza MIT
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

NOME="BiaAmp"
FRAMEWORK="/Library/Frameworks/Python.framework/Versions"

# ── Scelta dell'interprete ───────────────────────────────────────────────────
# py2app produce un'app autonoma solo da un Python "framework" (quello di
# python.org lo è; pyenv di norma no). Serve inoltre Python ≥ 3.10 (PyObjC 12).
# /usr/bin/python3 non viene mai interrogato: se gli strumenti da riga di
# comando di Apple non sono installati, eseguirlo apre una finestra di
# installazione.

versione_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
}

e_framework() {
    [ -n "$("$1" -c 'import sysconfig; print(sysconfig.get_config_var("PYTHONFRAMEWORK") or "")' 2>/dev/null)" ]
}

descrivi() {
    local tipo="non framework: non adatto a py2app"
    e_framework "$1" && tipo="framework"
    printf '     • %s → %s (%s)\n' "$1" "$("$1" --version 2>&1)" "$tipo"
}

PY_BASE="${PYTHON:-}"
if [ -z "$PY_BASE" ]; then
    for v in 3.14 3.13 3.12 3.11 3.10; do
        if [ -x "$FRAMEWORK/$v/bin/python3" ]; then
            PY_BASE="$FRAMEWORK/$v/bin/python3"
            break
        fi
    done
fi
if [ -z "$PY_BASE" ]; then
    candidato=$(command -v python3 || true)
    if [ -n "$candidato" ] && [ "$candidato" != "/usr/bin/python3" ] \
            && e_framework "$candidato" && versione_ok "$candidato"; then
        PY_BASE="$candidato"
    fi
fi

if [ -z "$PY_BASE" ] || [ ! -x "$PY_BASE" ] || ! versione_ok "$PY_BASE"; then
    echo "✗ Non trovo un Python adatto (python.org, versione 3.10 o successiva)."
    echo "  Versioni in $FRAMEWORK:"
    if [ -d "$FRAMEWORK" ]; then
        find "$FRAMEWORK" -mindepth 1 -maxdepth 1 -type d ! -name Current -exec basename {} \; | sed 's/^/     • /'
    else
        echo "     (cartella assente: il Python di python.org non è installato)"
    fi
    echo "  Altri python3 nel PATH:"
    which -a python3 2>/dev/null | awk '!visto[$0]++' | grep -vx /usr/bin/python3 \
        | while read -r p; do descrivi "$p"; done
    echo
    echo "  Soluzione: installa Python 3.14 con il pacchetto .pkg da"
    echo "  https://www.python.org/downloads/macos/  e rilancia:  bash build.sh"
    echo "  (oppure indica un interprete framework:  PYTHON=/percorso/python3 bash build.sh)"
    exit 1
fi
if ! e_framework "$PY_BASE"; then
    echo "⚠ $PY_BASE non è un Python framework: l'app potrebbe non essere autonoma."
fi
VERSIONE=$("$PY_BASE" -c 'import core; print(core.__version__)')
echo "BiaAmp $VERSIONE — $("$PY_BASE" --version) — $PY_BASE"

echo "▸ 1/6  Autotest del motore e controlli statici"
"$PY_BASE" core.py
"$PY_BASE" test/verifica_pyobjc.py

echo "▸ 2/6  Ambiente virtuale e dipendenze"
base_di() { "$1" -c 'import sys; print(sys.base_prefix)' 2>/dev/null; }
if [ -x .venv/bin/python ] && [ "$(base_di .venv/bin/python)" != "$(base_di "$PY_BASE")" ]; then
    echo "  .venv era stato creato con un altro Python: lo ricreo"
    rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
    "$PY_BASE" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

echo "▸ 3/6  Pulizia completa (build e dist)"
rm -rf build dist

echo "▸ 4/6  Costruzione di $NOME.app (registro in build.log)"
if ! .venv/bin/python setup.py py2app > build.log 2>&1; then
    echo "✗ py2app non è riuscito. Ultime righe di build.log:"
    tail -25 build.log
    exit 1
fi

echo "▸ 5/6  Firma ad hoc (necessaria su Apple Silicon)"
codesign --force --deep --sign - "dist/$NOME.app"
if codesign --verify --deep --strict "dist/$NOME.app"; then
    echo "  firma verificata"
else
    echo "  ⚠ verifica della firma non superata: conserva l'output qui sopra"
fi
# L'app gira solo sulle architetture dell'interprete usato (Homebrew: solo arm64;
# python.org "universal2": arm64 e x86_64).
echo "  architetture: $(file -b "dist/$NOME.app/Contents/MacOS/$NOME" | grep -oE 'arm64|x86_64' | sort -u | paste -sd ' ' -)"

echo "▸ 6/6  Pacchetti di distribuzione"
ditto -c -k --sequesterRsrc --keepParent "dist/$NOME.app" "dist/$NOME-$VERSIONE-macOS.zip"
STAGE=$(mktemp -d)
cp -R "dist/$NOME.app" "$STAGE/"
ln -s /Applications "$STAGE/Applicazioni"
hdiutil create -quiet -volname "$NOME $VERSIONE" -srcfolder "$STAGE" -ov -format UDZO \
    "dist/$NOME-$VERSIONE-macOS.dmg"
rm -rf "$STAGE"

echo
echo "✓ Fatto. Contenuto di dist/:"
ls -lh dist | sed 's/^/   /'
echo
echo "  Prova:      ./dist/$NOME.app/Contents/MacOS/$NOME     (errori visibili nel Terminale)"
echo "  Installa:   bash installa.sh"
