#!/usr/bin/env bash
# tg_publish.sh — Publica un archivo (APK/exe/ipa) en un canal de Telegram.
# Uso:
#   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx ./tg_publish.sh ARCHIVO "caption"
set -euo pipefail

# Forzar locale UTF-8 para evitar que bash/curl corrompan emojis y tildes
# (en Windows runners, Git Bash a veces arranca en locale C/POSIX)
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

FILE="${1:-}"
CAPTION="${2:-Nueva compilación}"

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "ℹ️  SKIP publicación en Telegram: faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
  exit 0
fi

if [ ! -f "$FILE" ]; then
  echo "ℹ️  SKIP publicación en Telegram: no existe $FILE"
  exit 0
fi

echo "📤 Publicando $FILE en Telegram..."

TMPLOG="$(mktemp /tmp/tg_publish.XXXXXX.log)"

# Pasamos el caption vía un archivo temporal en UTF-8 explícito, en vez de
# como argumento -F directo: esto evita que curl/el shell reinterpreten
# los bytes multibyte (emojis, tildes, guiones largos) con un locale erróneo.
CAPTION_FILE="$(mktemp /tmp/tg_caption.XXXXXX.txt)"
printf '%s' "$CAPTION" > "$CAPTION_FILE"
# Verificar que el archivo quedó en UTF-8 válido (si iconv falla, el caption
# tenía bytes inválidos desde el origen, no por culpa de curl)
if command -v iconv >/dev/null 2>&1; then
  iconv -f UTF-8 -t UTF-8 "$CAPTION_FILE" > /dev/null || {
    echo "⚠️  El caption no es UTF-8 válido antes de enviarlo. Revisa el origen del texto."
  }
fi

RESP="$(curl -sS --max-time 600 --retry 3 --retry-delay 5 --connect-timeout 20 \
  -F "chat_id=${TELEGRAM_CHAT_ID}" \
  -F "document=@${FILE}" \
  -F "caption=<${CAPTION_FILE};type=text/plain;charset=UTF-8" \
  -F "parse_mode=Markdown" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" 2>&1 | tee "$TMPLOG" || true)"

rm -f "$CAPTION_FILE"

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
