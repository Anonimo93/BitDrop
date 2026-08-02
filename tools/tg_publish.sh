#!/usr/bin/env bash
# tg_publish.sh — Publica un archivo (APK/exe/ipa) en un canal de Telegram.
# Uso:
#   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx ./tg_publish.sh ARCHIVO "caption"
set -euo pipefail

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

# Ruta absoluta: evita "curl (26) Failed to open/read local data" en
# Windows/Git Bash cuando curl.exe y bash no comparten el mismo cwd.
FILE="$(cd "$(dirname "$FILE_ARG")" && pwd)/$(basename "$FILE_ARG")"

if [ ! -r "$FILE" ]; then
  echo "❌ No se puede leer el archivo en ruta absoluta: $FILE"
  exit 1
fi

echo "📤 Publicando $FILE en Telegram..."
echo "   (tamaño: $(wc -c < "$FILE") bytes)"

TMPLOG="$(mktemp /tmp/tg_publish.XXXXXX.log)"

# El caption se escribe a un archivo en disco en vez de pasarlo por argv.
# En Windows, el proceso curl.exe (nativo o de Git) recibe los argumentos
# de línea de comandos a través de la API de Windows como una sola cadena
# que cada ejecutable reparsea con su propia rutina de C runtime; esa capa
# no siempre preserva bytes UTF-8 multi-byte (emojis, tildes) intactos.
# Escribiendo el caption a archivo con printf y leyéndolo con curl -F
# "campo=<archivo" evitamos ese paso de argv por completo: curl lee los
# bytes directo del archivo, tal cual fueron escritos por bash.
CAPTION_FILE="$(mktemp /tmp/tg_caption.XXXXXX.txt)"
printf '%s' "$CAPTION" > "$CAPTION_FILE"

RESP="$(curl -sS --max-time 600 --retry 3 --retry-delay 5 --connect-timeout 20 \
  -F "chat_id=${TELEGRAM_CHAT_ID}" \
  -F "parse_mode=Markdown" \
  -F "caption=<${CAPTION_FILE}" \
  -F "document=@${FILE}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" 2>&1 | tee "$TMPLOG" || true)"

rm -f "$CAPTION_FILE"

if echo "$RESP" | grep -q '"ok":true'; then
  echo "✅ Publicado en Telegram: $FILE"
  echo "Respuesta (ultimo 300 bytes):"
  tail -c 300 "$TMPLOG" || true
  rm -f "$TMPLOG"
else
  echo "⚠️  Telegram respondió (o curl falló). Ver log en $TMPLOG"
  echo "Salida breve:"
  echo "$RESP" | head -c 500
fi
