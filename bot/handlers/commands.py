"""
handlers/commands.py — Comandos generales del bot.

Fixes aplicados aquí:
  - BUG-02: /ls y /rm usan storage.list_user_files con mismo sort_by.
  - BUG-09: /clear_rev implementado con confirmación.
  - Aislamiento por usuario: cada comando sólo opera sobre archivos del usuario.
"""
from __future__ import annotations

import logging
import os
import time

from pyrogram import filters
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup)

from config import config
from database import db
from storage import storage
from utils import (admin_only, authorized_only, format_size, safe_handler)

logger = logging.getLogger(__name__)


def register(app) -> None:
    """Registra todos los handlers de comandos en la app."""

    # ── /start ──────────────────────────────────────────────────────
    @app.on_message(filters.command("start"))
    @safe_handler
    async def start_handler(client, message):
        user_id = message.from_user.id
        authorized = await db.is_authorized(user_id)
        if authorized:
            revistas = await db.list_revistas(only_active=True)
            rev_list = "\n".join(
                [f"  • **{r['rev_id']}**: {r['nombre']} (BitZero: {r.get('bitzero_mode', 0)})"
                 for r in revistas]
            ) or "  _(ninguna configurada)_"
            await message.reply(
                f"👋 **Bienvenido, {message.from_user.first_name}!**\n\n"
                "✅ **Estás autorizado para usar este bot.**\n\n"
                f"📚 **Revistas Disponibles:**\n{rev_list}\n\n"
                "🔧 **Comandos Disponibles:**\n"
                "• `/ls` - Ver tus archivos\n"
                "• `/rm <num>` - Eliminar archivo tuyo\n"
                "• `/deleteall` - Limpiar TODOS tus archivos\n"
                "• `/up` - Subir archivos a revista\n"
                "• `/clear_rev` - Limpiar archivos de una revista\n"
                "• `/zips <tamaño>` - Cambiar tamaño de partes\n"
                "• `/status` - Ver estado del sistema\n"
                "• `/bitzero <revista> <modo>` - Cambiar modo BitZero\n"
                "• `/bitzero_status` - Ver estado BitZero\n"
                "• `/test_bitzero <modo> [archivo]` - Probar codificación\n"
                "• `/history` - Ver tu historial de subidas\n"
                "• `/control_panel` - Panel de administración (solo admins)\n\n"
                f"💾 **Tu carpeta:** `raiz/{user_id}/`\n"
                f"📦 **Tamaño partes:** {config.CHUNK_SIZE_MB} MB\n\n"
                f"👨‍💻 **Desarrollador:** {config.DEVELOPER_HANDLE}"
            )
        else:
            await message.reply(
                "🔒 **Bot Privado**\n\n"
                "Este bot es de uso restringido.\n"
                "Para solicitar acceso, contacta al administrador:\n\n"
                f"👨‍💻 {config.DEVELOPER_HANDLE}\n\n"
                f"📋 **Tu ID:** `{user_id}`"
            )

    # ── /ls — listar archivos del usuario (BUG-02 fix) ──────────────
    @app.on_message(filters.command("ls"))
    @authorized_only
    @safe_handler
    async def list_files(client, message):
        user_id = message.from_user.id
        storage.ensure_user_dirs(user_id)
        files = storage.list_user_files(user_id, sort_by='modified_desc')
        if not files:
            await message.reply(
                f"📭 **Tu carpeta está vacía**\n\n"
                f"📁 **Directorio:** `raiz/{user_id}/`\n"
                "Envía archivos al bot para que aparezcan aquí."
            )
            return

        total_size = sum(f['size'] for f in files)
        text = f"📂 **Tus archivos**\n\n"
        text += f"📊 **Total:** {len(files)} archivos | {format_size(total_size)}\n\n"
        for i, f in enumerate(files[:20], 1):
            mod_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(f['modified']))
            text += f"{i}. **{f['name']}**\n   📏 {format_size(f['size'])} | 📅 {mod_time}\n\n"
        if len(files) > 20:
            text += f"... y {len(files) - 20} archivos más.\n\n"
        text += "💡 **Usa `/rm <número>` para eliminar un archivo.**\n"
        text += f"👨‍💻 **Desarrollador:** {config.DEVELOPER_HANDLE}"
        await message.reply(text)

    # ── /rm — borrar por índice (BUG-02 fix: mismo orden que /ls) ───
    @app.on_message(filters.command("rm"))
    @authorized_only
    @safe_handler
    async def remove_file(client, message):
        user_id = message.from_user.id
        try:
            parts = message.text.split()
            if len(parts) != 2:
                await message.reply("❌ **Uso:** `/rm <número>`")
                return
            idx = int(parts[1])
        except ValueError:
            await message.reply("❌ El número debe ser un entero.")
            return

        # BUG-02 fix: usar storage con mismo sort que /ls
        files = storage.list_user_files(user_id, sort_by='modified_desc')
        deleted = storage.delete_user_file_by_index(user_id, idx)
        if deleted:
            await message.reply(f"✅ **Eliminado:** `{deleted}`")
        else:
            await message.reply(f"❌ **Índice inválido.** Usa números del 1 al {len(files)}")

    # ── /deleteall — limpiar carpeta del usuario ────────────────────
    @app.on_message(filters.command("deleteall"))
    @authorized_only
    @safe_handler
    async def delete_all(client, message):
        user_id = message.from_user.id
        count = storage.delete_all_user_files(user_id)
        await message.reply(
            f"✅ **Limpieza completada**\n\n"
            f"🗑️ **Eliminados:** {count} elementos\n"
            f"📁 **Carpeta:** `raiz/{user_id}/`"
        )

    # ── /zips — cambiar tamaño de partes ────────────────────────────
    @app.on_message(filters.command("zips"))
    @authorized_only
    @safe_handler
    async def set_zip_size(client, message):
        parts = message.text.split()
        if len(parts) != 2:
            await message.reply("❌ **Uso:** `/zips <tamaño_MB>`")
            return
        try:
            new_size = int(parts[1])
        except ValueError:
            await message.reply("❌ El tamaño debe ser un entero.")
            return
        if not 1 <= new_size <= 100:
            await message.reply("❌ **El tamaño debe estar entre 1 y 100 MB.**")
            return
        config.CHUNK_SIZE_MB = new_size
        await message.reply(f"✅ **Tamaño de partes cambiado a {new_size} MB.**")

    # ── /status — estado del sistema ────────────────────────────────
    @app.on_message(filters.command("status"))
    @authorized_only
    @safe_handler
    async def status_handler(client, message):
        user_id = message.from_user.id
        user = await db.get_user(user_id)
        usage = storage.get_user_usage_bytes(user_id)
        quota_mb = (user.get('quota_mb', config.DEFAULT_USER_QUOTA_MB) if user
                    else config.DEFAULT_USER_QUOTA_MB)
        is_admin = config.is_admin(user_id)

        stats = await db.get_global_stats()
        revistas = await db.list_revistas()

        text = "📊 **Estado del Sistema**\n\n"
        text += "👤 **Tu cuenta:**\n"
        text += f"   • ID: `{user_id}`\n"
        text += f"   • Admin: {'✅ Sí' if is_admin else '❌ No'}\n"
        text += f"   • Espacio usado: {format_size(usage)}"
        if is_admin:
            text += " (ilimitado)\n"
        else:
            text += f" / {quota_mb} MB\n"
        text += "\n📈 **Global:**\n"
        text += f"   • Usuarios activos: {stats['active_users']}/{stats['total_users']}\n"
        text += f"   • Archivos totales: {stats['total_files']}\n"
        text += f"   • Espacio total: {format_size(stats['total_bytes'])}\n"
        text += f"   • Subidas exitosas: {stats['successful_uploads']}\n\n"
        text += "📚 **Revistas:**\n"
        for r in revistas:
            bz = "✅" if r.get('bitzero_mode', 0) > 0 else "❌"
            text += f"   • {r['nombre']}: BitZero {bz} (modo {r.get('bitzero_mode', 0)})\n"
        text += f"\n🕐 **Hora:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        text += f"👨‍💻 **Desarrollador:** {config.DEVELOPER_HANDLE}"
        await message.reply(text)

    # ── /bitzero — cambiar modo ─────────────────────────────────────
    @app.on_message(filters.command("bitzero"))
    @authorized_only
    @safe_handler
    async def bitzero_handler(client, message):
        parts = message.text.split()
        if len(parts) < 3:
            revistas = await db.list_revistas()
            rev_list = "\n".join(
                [f"  • `{r['rev_id']}` - {r['nombre']}" for r in revistas]
            )
            await message.reply(
                "❌ **Uso:** `/bitzero <revista> <modo>`\n\n"
                "**Revistas disponibles:**\n" + rev_list +
                "\n\n**Modos BitZero:**\n"
                "  • `0` - Sin ofuscación\n"
                "  • `1` - Ofuscación PNG\n"
                "  • `2` - Ofuscación HTML\n"
                "  • `3` - Ofuscación ZIP (encriptado AES)\n\n"
                f"👨‍💻 **Ejemplo:** `/bitzero KIKI_REV 2`"
            )
            return

        revista_id = parts[1].upper()
        try:
            modo = int(parts[2])
        except ValueError:
            await message.reply("❌ El modo debe ser 0, 1, 2 o 3.")
            return

        revista = await db.get_revista(revista_id)
        if not revista:
            revistas = await db.list_revistas()
            await message.reply(
                f"❌ **Revista no encontrada:** `{revista_id}`\n\n"
                f"Disponibles: {', '.join(r['rev_id'] for r in revistas)}"
            )
            return

        if modo not in [0, 1, 2, 3]:
            await message.reply("❌ **Modo inválido.** Modos válidos: 0, 1, 2, 3")
            return

        await db.update_revista_field(revista_id, 'bitzero_mode', modo)
        # Invalidar uploader en caché si existe
        from handlers.upload import invalidate_uploader
        invalidate_uploader(revista_id)

        nombres = {0: "Sin ofuscación", 1: "PNG", 2: "HTML", 3: "ZIP (AES)"}
        await message.reply(
            f"✅ **Modo BitZero actualizado**\n\n"
            f"📚 **Revista:** {revista['nombre']}\n"
            f"🔐 **Nuevo modo:** {modo} ({nombres[modo]})\n"
            f"🔄 **Próximas subidas** usarán este modo."
        )

    # ── /bitzero_status ──────────────────────────────────────────────
    # ── /up — Subir archivos por índice ─────────────────────────────
    @app.on_message(filters.command("up"))
    @authorized_only
    @safe_handler
    async def up_handler(client, message):
        user_id = message.from_user.id
        parts = message.text.split()
        
        files = storage.list_user_files(user_id, sort_by='modified_desc')
        if not files:
            await message.reply("📭 **No tienes archivos para subir.**")
            return

        # Si no hay argumentos, mostrar listado con índices para seleccionar
        if len(parts) == 1:
            text = "📤 **Selecciona archivos para subir (ej: /up 1,2)**\n\n"
            for i, f in enumerate(files[:20], 1):
                text += f"{i}. **{f['name']}** ({format_size(f['size'])})\n"
            await message.reply(text)
            return

        # Parsear índices (permitir 1,2,3 o 1 2 3)
        indices_str = " ".join(parts[1:]).replace(",", " ")
        try:
            selected_indices = [int(i) - 1 for i in indices_str.split()]
        except ValueError:
            await message.reply("❌ **Índices inválidos.** Usa números separados por espacios o comas.")
            return

        selected_files = []
        for idx in selected_indices:
            if 0 <= idx < len(files):
                selected_files.append(files[idx])
            else:
                await message.reply(f"❌ **Índice inválido:** `{idx + 1}`")
                return

        # Mostrar revistas para subir
        revistas = await db.list_revistas(only_active=True)
        if not revistas:
            await message.reply("❌ **No hay revistas activas.**")
            return

        keyboard = []
        # serializar índices como 1-2-3
        idx_str = "-".join([str(i) for i in selected_indices])
        for r in revistas:
            keyboard.append([InlineKeyboardButton(
                f"📚 {r['nombre']}",
                callback_data=f"up_select_{r['rev_id']}_{idx_str}"
            )])
        keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel_action")])

        await message.reply(
            f"✅ **Seleccionados {len(selected_files)} archivos.**\n\n"
            "Elige la revista donde subirlos:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    @app.on_message(filters.command("bitzero_status"))
    @authorized_only
    @safe_handler
    async def bitzero_status_handler(client, message):
        revistas = await db.list_revistas()
        text = "🔐 **Estado BitZero por Revista**\n\n"
        for r in revistas:
            modo = r.get('bitzero_mode', 0)
            info = {
                0: "❌ **Desactivado**",
                1: "🖼️ **PNG** (ofuscación básica)",
                2: "🌐 **HTML** (ofuscación avanzada)",
                3: "📦 **ZIP** (encriptado AES-256)"
            }.get(modo, f"❓ Modo {modo}")
            text += f"📚 **{r['nombre']}** (`{r['rev_id']}`)\n"
            text += f"   🔐 Modo: {info}\n"
            text += f"   🔑 Clave: {'✅' if r.get('encryption_key') else '❌'}\n\n"
        text += "💡 **Cambiar modo:** `/bitzero <revista> <modo>`\n"
        text += f"👨‍💻 **Desarrollador:** {config.DEVELOPER_HANDLE}"
        await message.reply(text)

    # ── /history — historial de subidas del usuario ─────────────────
    @app.on_message(filters.command("history"))
    @authorized_only
    @safe_handler
    async def history_handler(client, message):
        user_id = message.from_user.id
        uploads = await db.list_user_uploads(user_id, limit=10)
        if not uploads:
            await message.reply("📭 **No tienes subidas registradas.**")
            return
        text = "📋 **Tus últimas subidas**\n\n"
        for i, u in enumerate(uploads, 1):
            text += (
                f"{i}. **{u['original_name']}**\n"
                f"   📚 {u['revista_id']} | 🔐 modo {u['bitzero_mode']} | "
                f"📏 {format_size(u['original_size'])}\n"
                f"   📅 {u['uploaded_at']} | ✅ {u['status']}\n\n"
            )
        await message.reply(text)

    # ── /clear_rev — BUG-09 fix: implementado con confirmación ──────
    @app.on_message(filters.command("clear_rev"))
    @authorized_only
    @safe_handler
    async def clear_rev_handler(client, message):
        revistas = await db.list_revistas(only_active=True)
        if not revistas:
            await message.reply("📭 No hay revistas configuradas.")
            return
        keyboard = []
        for r in revistas:
            keyboard.append([InlineKeyboardButton(
                f"📚 {r['nombre']} (sub:{r['submission_id']})",
                callback_data=f"clear_rev_{r['rev_id']}"
            )])
        keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel_action")])
        await message.reply(
            "⚠️ **Limpiar archivos de revista**\n\n"
            "Esto eliminará TODOS los archivos subidos a la submission seleccionada.\n"
            "Selecciona la revista:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # ── /test_bitzero ────────────────────────────────────────────────
    @app.on_message(filters.command("test_bitzero"))
    @authorized_only
    @safe_handler
    async def test_bitzero_handler(client, message):
        from encoder import BitZeroEncoder
        user_id = message.from_user.id

        parts = message.text.split()
        if len(parts) < 2:
            await message.reply(
                "❌ **Uso:** `/test_bitzero <modo> [archivo_num]`\n\n"
                "**Modos:** 1=PNG, 2=HTML, 3=ZIP\n\n"
                "**Ejemplo:** `/test_bitzero 2 1`"
            )
            return

        try:
            modo = int(parts[1])
            archivo_idx = int(parts[2]) - 1 if len(parts) > 2 else 0
        except ValueError:
            await message.reply("❌ Modo e índice deben ser enteros.")
            return

        if modo not in [1, 2, 3]:
            await message.reply("❌ Modo inválido. Usa 1, 2 o 3.")
            return

        files = storage.list_user_files(user_id, sort_by='modified_desc')
        if not files:
            await message.reply("📭 **No tienes archivos para probar.**")
            return

        if archivo_idx < 0 or archivo_idx >= len(files):
            await message.reply(f"❌ Índice inválido. Usa 1 a {len(files)}")
            return

        file_path = files[archivo_idx]['path']
        file_name = files[archivo_idx]['name']
        original_size = os.path.getsize(file_path)

        status_msg = await message.reply(
            f"🧪 **Probando BitZero Modo {modo}**\n\n"
            f"📄 **Archivo:** `{file_name}`\n"
            f"💾 **Tamaño:** {format_size(original_size)}\n"
            f"⏳ Procesando..."
        )

        temp_dir = storage.get_user_dir(user_id, 'temp')
        os.makedirs(temp_dir, exist_ok=True)

        try:
            if modo == 1:
                output_path = os.path.join(temp_dir, f"{file_name}.test.png")
                success = BitZeroEncoder.encode_png(file_path, output_path)
                tipo = "PNG"
            elif modo == 2:
                output_path = os.path.join(temp_dir, f"{file_name}.test.html")
                # Buscar primera revista con encryption_key
                revistas = await db.list_revistas()
                enc_key = next((r['encryption_key'] for r in revistas
                                if r.get('encryption_key')), None)
                success = BitZeroEncoder.encode_html(file_path, output_path, enc_key)
                tipo = "HTML"
            else:  # modo 3
                output_path = os.path.join(temp_dir, f"{file_name}.test.zip")
                success = BitZeroEncoder.encode_zip(file_path, output_path, "test_pass_123")
                tipo = "ZIP"

            if success:
                output_size = os.path.getsize(output_path)
                ratio = (output_size / original_size) * 100 if original_size else 0
                await status_msg.edit_text(
                    f"✅ **Prueba BitZero completada**\n\n"
                    f"📄 **Archivo:** `{file_name}`\n"
                    f"🔧 **Modo:** {modo} ({tipo})\n"
                    f"📊 **Original:** {format_size(original_size)}\n"
                    f"📈 **Codificado:** {format_size(output_size)}\n"
                    f"📉 **Ratio:** {ratio:.1f}%"
                )
                if output_size < 50 * 1024 * 1024:
                    await client.send_document(
                        chat_id=message.chat.id,
                        document=output_path,
                        caption=f"🧪 Archivo de prueba BitZero Modo {modo}"
                    )
            else:
                await status_msg.edit_text(f"❌ **Error en codificación modo {modo}**")
        finally:
            # Limpiar temporal tras 60s
            import asyncio as _aio
            async def _cleanup():
                await _aio.sleep(60)
                try:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                except Exception:
                    pass
            _aio.create_task(_cleanup())
