#!/usr/bin/env python3
"""
publish_to_telegram.py — Publica un archivo (APK, .exe, .ipa...) en un
canal/grupo de Telegram usando la Bot API. Sin dependencias (solo stdlib).

Uso:
    python3 publish_to_telegram.py ARCHIVO "caption opcional" \
        --token BOT_TOKEN --chat CHAT_ID

También lee las variables de entorno TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
(las usa el CI de GitHub Actions).

Cómo crear el bot y obtener el chat:
  1. En Telegram habla con @BotFather → /newbot → te da el TOKEN.
  2. Añade el bot como ADMINISTRADOR de tu canal.
  3. Chat ID: si el canal es público usa @nombrecanal.
     Si es privado, envía cualquier mensaje al canal y luego a @userinfobot
     (o llama a getUpdates con el token) para obtener el ID (-100xxxxxxxxxx).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.request

BOUNDARY = "----BitDropBoundary"


def send_document(token: str, chat_id: str, file_path: str,
                  caption: str = "") -> dict:
    """Envía un archivo como documento. Devuelve la respuesta JSON de Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    fname = os.path.basename(file_path)
    mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        content = f.read()

    def field(name: str, value: str) -> bytes:
        return (f"--{BOUNDARY}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode("utf-8")

    body = b"".join([
        field("chat_id", str(chat_id)),
        field("caption", caption) if caption else b"",
        (f"--{BOUNDARY}\r\n"
         f"Content-Disposition: form-data; name=\"document\"; filename=\"{fname}\"\r\n"
         f"Content-Type: {mime}\r\n\r\n").encode("utf-8"),
        content,
        f"\r\n--{BOUNDARY}--\r\n".encode("utf-8"),
    ])

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publica un archivo en Telegram")
    parser.add_argument("file", help="Ruta al archivo a subir (APK/exe/ipa...)")
    parser.add_argument("caption", nargs="?", default="", help="Texto del mensaje")
    parser.add_argument("--token", default=os.getenv("TELEGRAM_BOT_TOKEN", ""))
    parser.add_argument("--chat", default=os.getenv("TELEGRAM_CHAT_ID", ""))
    args = parser.parse_args()

    if not args.token or not args.chat:
        print("❌ Faltan TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID "
              "(pásalos con --token/--chat o como variables de entorno)")
        return 1
    if not os.path.isfile(args.file):
        print(f"❌ No existe el archivo: {args.file}")
        return 1

    try:
        resp = send_document(args.token, args.chat, args.file, args.caption)
    except Exception as e:
        print(f"❌ Error enviando a Telegram: {e}")
        return 1

    if resp.get("ok"):
        print(f"✅ Publicado en Telegram: {args.file}")
        return 0
    print(f"❌ Telegram respondió ok=false: {resp}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
