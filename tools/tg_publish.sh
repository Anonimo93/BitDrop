#!/usr/bin/env bash
# tg_publish.sh — Publica un archivo (APK/exe/ipa) en un canal de Telegram.
# Usado por los workflows de GitHub Actions. Se salta si no hay credenciales.
#
# Uso:
#   TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx ./tg_publish.sh ARCHIVO "caption"
set -euo pipefail

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
RESP="$(curl -sS -F "chat_id=${TELEGRAM_CHAT_ID}" \
  -F "document=@${FILE}" \
  -F "caption=${CAPTION}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendDocument" || true)"

if echo "$RESP" | grep -q '"ok":true'; then
  echo "✅ Publicado en Telegram: $FILE"
else
  echo "⚠️  Telegram respondió: $(echo "$RESP" | head -c 300)"
fi
