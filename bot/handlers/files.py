"""
handlers/files.py — Recepción de archivos desde Telegram.

Cada archivo se guarda en la carpeta del usuario (aislamiento).
Se verifica quota antes de aceptar. Se registra en DB.

Mejoras:
  - Si STORAGE_CHANNEL_ID está configurado, se REENVÍA el archivo al
    canal privado de respaldo sin descargarlo (usa file_id del message),
    evitando consumir megas del usuario. Se guarda storage_msg_id en DB.
  - Si LOG_CHANNEL_ID está configurado, se envía un mensaje de auditoría
    (texto) al canal de log por cada archivo recibido.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from pyrogram import filters
from pyrogram.errors import RPCError

from config import config
from database import db
from storage import storage
from utils import (authorized_only, format_size, progress_bar, safe_handler)

logger = logging.getLogger(__name__)


async def _forward_to_storage_channel(client, message, user_id: int,
                                       file_name: str, file_size: int) -> tuple:
    """Reenvía el mensaje original al canal de almacenamiento.
    Devuelve (storage_msg_id, storage_channel_id) o (None, None) si falla.
    No descarga el archivo: usa forward_message que NO consume ancho de banda
    del usuario (Telegram copia server-side).
    """
    if not config.STORAGE_CHANNEL_ID:
        return None, None
    try:
        forwarded = await client.forward_messages(
            chat_id=config.STORAGE_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        # forwarded puede ser un solo Message o una lista
        if isinstance(forwarded, list):
            forwarded = forwarded[0] if forwarded else None
        if forwarded and forwarded.id:
            return forwarded.id, config.STORAGE_CHANNEL_ID
    except RPCError as e:
        logger.warning(f"No se pudo reenviar al canal de almacenamiento: {e}")
    except Exception as e:
        logger.warning(f"Error inesperado en forward: {e}")
    return None, None


async def _log_to_channel(client, text: str) -> None:
    """Envía un mensaje de texto al canal de log (auditoría)."""
    if not config.LOG_CHANNEL_ID:
        return
    try:
        await client.send_message(
            chat_id=config.LOG_CHANNEL_ID,
            text=text,
            disable_web_page_preview=True
        )
    except RPCError as e:
        logger.warning(f"No se pudo enviar al canal de log: {e}")
    except Exception as e:
        logger.warning(f"Error inesperado en log channel: {e}")


def register(app) -> None:

    @app.on_message(filters.document | filters.video | filters.audio | filters.photo)
    @authorized_only
    @safe_handler
    async def save_received_file(client, message):
        user_id = message.from_user.id
        storage.ensure_user_dirs(user_id)

        # ── Determinar nombre ────────────────────────────────────────
        file_name = None
        mime_type = None

        if message.document:
            file_name = message.document.file_name or f"document_{message.id}"
            mime_type = message.document.mime_type
            if not os.path.splitext(file_name)[1] and mime_type:
                ext_map = {
                    'application/pdf': '.pdf',
                    'application/zip': '.zip',
                    'text/plain': '.txt',
                    'image/jpeg': '.jpg',
                    'image/png': '.png',
                    'video/mp4': '.mp4',
                    'audio/mpeg': '.mp3',
                }
                ext = ext_map.get(mime_type, '.bin')
                if not file_name.endswith(ext):
                    file_name += ext
        elif message.video:
            file_name = message.video.file_name or f"video_{message.id}.mp4"
            mime_type = 'video/mp4'
        elif message.audio:
            file_name = message.audio.file_name or f"audio_{message.id}.mp3"
            mime_type = 'audio/mpeg'
        elif message.photo:
            file_name = f"photo_{message.photo.file_id[:12]}.jpg"
            mime_type = 'image/jpeg'
        else:
            file_name = f"file_{message.id}.bin"

        file_name = storage.sanitize_filename(file_name)

        # ── Determinar tamaño y verificar quota ──────────────────────
        try:
            expected_size = 0
            for attr in ('document', 'video', 'audio', 'photo'):
                obj = getattr(message, attr, None)
                if obj and hasattr(obj, 'file_size'):
                    expected_size = obj.file_size or 0
                    break
        except Exception:
            expected_size = 0

        user = await db.get_user(user_id)
        # Auto-registrar si no existe (ej. admin no registrado aún)
        if user is None:
            username = message.from_user.username or None
            await db.add_user(user_id, username, added_by=0)
            user = await db.get_user(user_id)
        quota_mb = (user.get('quota_mb', config.DEFAULT_USER_QUOTA_MB) if user
                    else config.DEFAULT_USER_QUOTA_MB)
        is_admin = config.is_admin(user_id)
        if not is_admin and not storage.check_quota(user_id, expected_size, quota_mb):
            used = storage.get_user_usage_bytes(user_id)
            await message.reply(
                f"❌ **Cuota excedida**\n\n"
                f"📊 Tu quota: {quota_mb} MB\n"
                f"💾 Usado: {format_size(used)}\n"
                f"📥 Este archivo: {format_size(expected_size)}\n\n"
                f"Elimina archivos con `/rm` o pide al admin ampliar tu quota."
            )
            return

        # ── Generar ruta única en carpeta del usuario ────────────────
        user_dir = storage.get_user_dir(user_id)
        file_path = storage.unique_path(user_dir, file_name)

        status_msg = await message.reply(
            f"📥 **Descargando...**\n\n"
            f"📄 `{os.path.basename(file_path)}`\n"
            f"⏳ Iniciando..."
        )

        last_update = time.time()
        last_percent = -1.0

        async def progress(current, total):
            nonlocal last_update, last_percent
            now = time.time()
            percent = (current / total) * 100 if total else 0
            if (now - last_update >= 2 or abs(percent - last_percent) >= 5
                    or current == total):
                try:
                    bar = progress_bar(current, total)
                    elapsed = now - status_msg.date.timestamp()
                    speed = (current / 1024 / 1024 / elapsed) if elapsed > 0 else 0
                    remaining = ((total - current) / (speed * 1024 * 1024)) if speed > 0 else 0
                    await status_msg.edit_text(
                        f"📥 **Descargando...**\n\n"
                        f"📄 `{os.path.basename(file_path)}`\n"
                        f"📊 {bar}\n"
                        f"💾 {format_size(current)} / {format_size(total)}\n"
                        f"⚡ {speed:.2f} MB/s\n"
                        f"⏱️ {remaining:.0f}s restantes"
                    )
                    last_update = now
                    last_percent = percent
                except Exception as e:
                    logger.debug(f"progress update error: {e}")

        # ── Descarga con reintentos ─────────────────────────────────
        max_attempts = config.MAX_DOWNLOAD_ATTEMPTS
        attempt = 1
        success = False
        while attempt <= max_attempts:
            try:
                await message.download(
                    file_name=file_path,
                    progress=progress,
                    block=True
                )
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    success = True
                    break
                logger.warning(f"Descarga intento {attempt}: archivo 0 bytes o ausente")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout intento {attempt}")
                await status_msg.edit_text(
                    f"⚠️ **Timeout (intento {attempt}/{max_attempts})**\nReintentando en 5s..."
                )
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error intento {attempt}: {e}")
                await status_msg.edit_text(
                    f"⚠️ **Error (intento {attempt}/{max_attempts})**\n{str(e)[:100]}\n"
                    f"Reintentando en 5s..."
                )
                await asyncio.sleep(5)
            attempt += 1

        if not success:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            await status_msg.edit_text(
                f"❌ **No se pudo descargar**\n\n"
                f"📄 `{file_name}`\n"
                f"🔄 Intentos fallidos: {max_attempts}\n\n"
                "Posibles causas: conexión inestable, timeout del servidor."
            )
            return

        # ── Reenviar al canal de almacenamiento (sin consumir megas) ─
        storage_msg_id = None
        storage_channel_id = None
        storage_msg_id, storage_channel_id = await _forward_to_storage_channel(
            client, message, user_id, file_name, expected_size
        )

        # ── Registrar en DB ──────────────────────────────────────────
        file_size = os.path.getsize(file_path)
        await db.register_file(
            user_id=user_id,
            file_name=os.path.basename(file_path),
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            tg_message_id=message.id,
            storage_msg_id=storage_msg_id,
            storage_channel_id=storage_channel_id,
        )

        # ── Enviar al canal de LOG (auditoría) ──────────────────────
        user_display = message.from_user.username or message.from_user.first_name or str(user_id)
        log_text = (
            f"📥 **Archivo recibido**\n\n"
            f"👤 **Usuario:** @{user_display}\n"
            f"🆔 **ID:** `{user_id}`\n"
            f"📄 **Archivo:** `{os.path.basename(file_path)}`\n"
            f"💾 **Tamaño:** {format_size(file_size)}\n"
            f"🗂️ **MIME:** `{mime_type or 'N/A'}`\n"
            f"📅 **Fecha:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"💬 **Mensaje ID:** `{message.id}`"
        )
        if storage_msg_id:
            log_text += f"\n📦 **Respaldo:** canal `{storage_channel_id}` msg `{storage_msg_id}`"
        await _log_to_channel(client, log_text)

        await status_msg.edit_text(
            f"✅ **Archivo guardado**\n\n"
            f"📄 `{os.path.basename(file_path)}`\n"
            f"💾 {format_size(file_size)}\n"
            f"📁 `raiz/{user_id}/`\n"
            f"🔄 Intentos: {attempt}"
            + (f"\n📦 Respaldo en canal" if storage_msg_id else "")
        )
