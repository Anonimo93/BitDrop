#!/usr/bin/env bash
# tg_publish.sh — Publica un archivo (APK/exe/ipa) en un canal de Telegram.
# Uso:
#   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx ./tg_publish.sh ARCHIVO "caption"
set -euo pipefail

# Forzar locale UTF-8: en Windows runners, Git Bash a veces arranca en
# locale C/POSIX y eso corrompe la codificación de emojis/tildes al pasar
# por argv hacia curl, causando "strings must be encoded in UTF-8".
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

FILE_ARG="${1:-}"
CAPTION="${2:-Nueva compilación}"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "ℹ️  SKIP publicación en Telegram: faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
  exit 0
fi

if [ ! -f "$FILE_ARG" ]; then
  echo "ℹ️  SKIP publicación en Telegram: no existe $FILE_ARG"
  exit 0
fi

# Resolver a ruta absoluta: en Windows/Git Bash, curl (nativo o de Git)
# puede fallar con "(26) Failed to open/read local data from file" si el
# working directory de curl no coincide exactamente con el del script al
# usar rutas relativas. Con ruta absoluta se elimina esa ambigüedad.
FILE="$(cd "$(dirname "$FILE_ARG")" && pwd)/$(basename "$FILE_ARG")"

# Verificación explícita de que el archivo es legible con esa ruta absoluta,
# para dar un error claro en vez del críptico curl (26) si algo sigue mal.
if [ ! -r "$FILE" ]; then
  echo "❌ No se puede leer el archivo en ruta absoluta: $FILE"
  exit 1
fi

echo "📤 Publicando $FILE en Telegram..."
echo "   (tamaño: $(wc -c < "$FILE") bytes)"

TMPLOG="$(mktemp /tmp/tg_publish.XXXXXX.log)"

# --form-string para el caption: envía el valor literal sin que curl lo
# reinterprete (evita el error "strings must be encoded in UTF-8" causado
# por locale incorrecto en versiones previas de este script).
RESP="$(curl -sS --max-time 600 --retry 3 --retry-delay 5 --connect-timeout 20 \
  -F "chat_id=${TELEGRAM_CHAT_ID}" \
  -F "document=@${FILE}" \
  -F "parse_mode=Markdown" \
  --form-string "caption=${CAPTION}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" 2>&1 | tee "$TMPLOG" || true)"

# show short preview
if echo "$RESP" | grep -q '"ok":true'; then
  echo "✅ Publicado en Telegram: $FILE"
  echo "Respuesta (ultimo 300 bytes):"
  tail -c 300 "$TMPLOG" || true
  rm -f "$TMPLOG"
else
  echo "⚠️  Telegram respondió (o curl falló). Ver log en $TMPLOG"
  echo "Salida breve:"
  echo "$RESP" | head -c 500
  # keep TMPLOG for debugging
fi
