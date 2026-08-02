"""
shortener.py — Mini acortador de URLs BitZero (integrado en el bot).

Genera códigos cortos aleatorios (8 chars, base62) que mapean a la URL
BitZero completa. El código NO contiene información sensible: las
credenciales quedan guardadas en la DB del bot.

Servidor HTTP (aiohttp, corre como task dentro del loop del bot):
  - GET /api/resolve?code=XXXX  → JSON {"ok": true, "url": "..."}
                                   (lo usa el downloader bitzero.py)
  - GET /<code>                 → 302 redirect a la URL completa
                                   (para navegadores / humanos)
  - GET /health                 → {"ok": true}

Requiere que el dominio público (SHORT_URL_BASE, ej: https://btz.dwn)
apunte al servidor donde corre el bot.
"""
from __future__ import annotations

import logging
import secrets
import time

from aiohttp import web

from config import config
from database import db

logger = logging.getLogger(__name__)

# Alfabeto base62 (sin caracteres ambiguos tipo l/O/0/I para evitar
# confusiones al copiar/pegar la URL manualmente).
CODE_ALPHABET = "23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8


def generate_code() -> str:
    """Genera un código corto aleatorio criptográficamente seguro."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _is_expired(row: dict) -> bool:
    exp = row.get('expires_at')
    return bool(exp) and exp < _now()


async def shorten_url(full_url: str, user_id: int) -> str:
    """Acorta una URL BitZero completa. Devuelve la URL corta o \"\"
    si no se pudo (shortener no configurado / error de DB)."""
    base = (config.SHORT_URL_BASE or "").strip().rstrip('/')
    if not base or not full_url:
        return ""
    try:
        # Limpieza oportunista de códigos expirados (evita crecimiento infinito)
        try:
            await db.prune_expired_short_urls()
        except Exception:
            pass

        # Reutilizar código si ya existe para la misma URL (evita duplicados)
        existing = await db.find_short_url_by_full_url(full_url)
        if existing:
            return f"{base}/{existing['code']}"

        ttl_days = config.SHORT_URL_TTL_DAYS
        expires_at = None
        if ttl_days and ttl_days > 0:
            expires_at = time.strftime(
                '%Y-%m-%d %H:%M:%S',
                time.localtime(time.time() + ttl_days * 86400)
            )

        for _ in range(5):  # reintentar en caso de colisión
            code = generate_code()
            if await db.create_short_url(code, full_url, user_id, expires_at):
                logger.info(f"URL corta creada: {base}/{code}")
                return f"{base}/{code}"
        logger.error("No se pudo generar un código único tras 5 intentos")
    except Exception as e:
        logger.error(f"shorten_url error: {e}")
    return ""


# ── Servidor HTTP ───────────────────────────────────────────────────
async def handle_resolve(request: web.Request) -> web.Response:
    """GET /api/resolve?code=XXXX → JSON con la URL completa."""
    code = (request.query.get("code") or "").strip()
    if not code:
        return web.json_response({"ok": False, "error": "missing code"}, status=400)
    row = await db.get_short_url(code)
    if not row:
        return web.json_response({"ok": False, "error": "code not found"}, status=404)
    if _is_expired(row):
        return web.json_response({"ok": False, "error": "code expired"}, status=410)
    return web.json_response({"ok": True, "url": row["full_url"]})


async def handle_redirect(request: web.Request) -> web.Response:
    """GET /<code> → 302 a la URL completa (para humanos/navegador)."""
    code = (request.match_info.get("code") or "").strip()
    # Guard defensivo: los códigos son SIEMPRE de CODE_LENGTH chars alfanuméricos.
    # Evita que rutas tipo /health o /api caigan aquí según el orden de rutas
    # de aiohttp (defensa en profundidad).
    if len(code) != CODE_LENGTH or not code.isalnum():
        return web.Response(
            text="<h3>404 — Código no encontrado o expirado</h3>",
            status=404, content_type='text/html'
        )
    row = await db.get_short_url(code)
    if not row or _is_expired(row):
        return web.Response(
            text="<h3>404 — Código no encontrado o expirado</h3>",
            status=404, content_type='text/html'
        )
    return web.Response(status=302, headers={"Location": row["full_url"]})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "bitzero-shortener"})


async def run_shortener_server() -> None:
    """Arranca el servidor aiohttp. No bloquea: el TCPSite queda sirviendo
    en background dentro del event loop del bot."""
    try:
        app = web.Application()
        app.router.add_get("/api/resolve", handle_resolve)
        app.router.add_get("/health", handle_health)
        app.router.add_get("/{code}", handle_redirect)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=config.SHORTENER_HOST, port=config.SHORTENER_PORT)
        await site.start()
        logger.info(f"🔗 Shortener HTTP escuchando en "
                    f"http://{config.SHORTENER_HOST}:{config.SHORTENER_PORT} "
                    f"(base pública: {config.SHORT_URL_BASE})")
    except Exception as e:
        logger.error(f"No se pudo iniciar el shortener HTTP: {e}")
