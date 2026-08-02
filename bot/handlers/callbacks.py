"""
handlers/callbacks.py — Manejador central de callbacks (inline keyboards).

Rutas:
  - upload_select_<rev_id>     → inicia subida a revista
  - clear_rev_<rev_id>         → pide confirmación para limpiar
  - clear_rev_confirm_<rev_id> → ejecuta limpieza
  - cancel_action              → cancela operación
  - cp_edit_<rev_id>           → muestra menú de campos de revista
  - cp_field_<rev_id>_<campo>  → pide nuevo valor
  - cp_test_<rev_id>           → prueba login
  - cp_back / cp_close         → navegación del panel
"""
from __future__ import annotations

import logging

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from database import db
from storage import storage
from utils import set_admin_state, safe_handler
from handlers.upload import get_or_create_uploader, perform_upload_with_bitzero

logger = logging.getLogger(__name__)

# Campos editables del panel de control. Pueden contener '_' (encryption_key,
# submission_id) igual que los rev_id (KIKI_REV), por eso el parsing de
# cp_field_ busca estos nombres desde el final del callback_data.
EDITABLE_FIELDS = (
    "username", "password", "submission_id", "bitzero",
    "encryption_key", "base_url", "contexto", "nombre",
)


def register(app) -> None:

    @app.on_callback_query()
    @safe_handler
    async def handle_callback(client, callback_query):
        data = callback_query.data or ""
        user_id = callback_query.from_user.id

        # Verificación de autorización
        if not await db.is_authorized(user_id):
            await callback_query.answer("❌ No autorizado", show_alert=True)
            return

        # ── Cancelar ────────────────────────────────────────────────
        if data == "cancel_action":
            await callback_query.message.edit_text("❌ **Operación cancelada.**")
            await callback_query.answer()
            return

        # ── Subir a revista ─────────────────────────────────────────
        # ── Subir a revista (por índices) ───────────────────────────
        if data.startswith("up_select_"):
            # formato: up_select_<rev_id>_<idx1>-<idx2>...
            parts = data.split("_")
            # El ID de la revista podría tener guiones, así que tomamos desde la parte 2 hasta la última (que es el índice)
            revista_id = "_".join(parts[2:-1])
            indices_str = parts[-1]
            
            revista = await db.get_revista(revista_id)
            if not revista:
                await callback_query.answer("❌ Revista no encontrada", show_alert=True)
                return
            
            indices = [int(i) for i in indices_str.split("-")]
            files = storage.list_user_files(user_id, sort_by='modified_desc')
            file_paths = [files[i]['path'] for i in indices if 0 <= i < len(files)]
            
            uploader = await get_or_create_uploader(revista)
            await callback_query.message.edit_text(f"🚀 **Subiendo {len(file_paths)} archivos a {revista['nombre']}...**")
            
            await perform_upload_with_bitzero(
                client, callback_query.message, uploader, revista, user_id, file_paths=file_paths
            )
            return

        if data.startswith("upload_select_"):
            revista_id = data.replace("upload_select_", "")
            revista = await db.get_revista(revista_id)
            if not revista:
                await callback_query.answer("❌ Revista no encontrada", show_alert=True)
                return

            uploader = await get_or_create_uploader(revista)

            await callback_query.answer(f"Subiendo a {revista['nombre']}...")
            await callback_query.message.edit_text(
                f"🚀 **Iniciando subida**\n\n"
                f"📚 **Revista:** {revista['nombre']}\n"
                f"🔐 **BitZero:** {'✅' if revista.get('bitzero_mode', 0) > 0 else '❌'}"
                f" (modo {revista.get('bitzero_mode', 0)})\n"
                f"⏳ Conectando..."
            )
            await perform_upload_with_bitzero(
                client, callback_query.message, uploader, revista, user_id
            )
            return

        # ── clear_rev: pedir confirmación (BUG-09 fix) ──────────────
        if data.startswith("clear_rev_confirm_"):
            revista_id = data.replace("clear_rev_confirm_", "")
            revista = await db.get_revista(revista_id)
            if not revista:
                await callback_query.answer("Revista no encontrada", show_alert=True)
                return
            # TODO: implementar borrado via API OJS (DELETE a cada file_id)
            # Por ahora, mensaje informativo
            await callback_query.message.edit_text(
                f"⚠️ **Función en desarrollo**\n\n"
                f"La limpieza de archivos de la revista {revista['nombre']} "
                f"requiere implementar DELETE a la API OJS. "
                f"Por ahora, gestiona los archivos desde el panel web de OJS."
            )
            await callback_query.answer()
            return

        if data.startswith("clear_rev_"):
            revista_id = data.replace("clear_rev_", "")
            revista = await db.get_revista(revista_id)
            if not revista:
                await callback_query.answer("Revista no encontrada", show_alert=True)
                return
            keyboard = [
                [InlineKeyboardButton(
                    f"✅ Sí, eliminar archivos de {revista['nombre']}",
                    callback_data=f"clear_rev_confirm_{revista_id}"
                )],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_action")],
            ]
            await callback_query.message.edit_text(
                f"⚠️ **Confirmar limpieza**\n\n"
                f"📚 Revista: {revista['nombre']}\n"
                f"🆔 Submission: `{revista['submission_id']}`\n\n"
                f"Esto eliminará TODOS los archivos subidos a esta submission. "
                f"¿Continuar?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await callback_query.answer()
            return

        # ── Panel de control ────────────────────────────────────────
        if not config.is_admin(user_id):
            await callback_query.answer("❌ Solo admin", show_alert=True)
            return

        if data.startswith("cp_edit_"):
            rev_id = data.replace("cp_edit_", "")
            revista = await db.get_revista(rev_id)
            if not revista:
                await callback_query.answer("Revista no encontrada", show_alert=True)
                return
            keyboard = [
                [InlineKeyboardButton(f"👤 Usuario: {revista['username']}",
                                      callback_data=f"cp_field_{rev_id}_username")],
                [InlineKeyboardButton(f"🔑 Contraseña: {revista['password'][:3]}***",
                                      callback_data=f"cp_field_{rev_id}_password")],
                [InlineKeyboardButton(f"🆔 Submission ID: {revista['submission_id']}",
                                      callback_data=f"cp_field_{rev_id}_submission_id")],
                [InlineKeyboardButton(f"🔐 Modo BitZero: {revista.get('bitzero_mode', 0)}",
                                      callback_data=f"cp_field_{rev_id}_bitzero")],
                [InlineKeyboardButton(
                    f"🔑 Clave: {(revista.get('encryption_key') or 'No configurada')[:10]}",
                    callback_data=f"cp_field_{rev_id}_encryption_key")],
                [InlineKeyboardButton(f"🌐 URL: {revista['base_url']}",
                                      callback_data=f"cp_field_{rev_id}_base_url")],
                [InlineKeyboardButton(f"📝 Contexto: {revista['contexto']}",
                                      callback_data=f"cp_field_{rev_id}_contexto")],
                [InlineKeyboardButton("🧪 Probar conexión",
                                      callback_data=f"cp_test_{rev_id}")],
                [InlineKeyboardButton("🔙 Volver", callback_data="cp_back")],
                [InlineKeyboardButton("❌ Cerrar", callback_data="cp_close")],
            ]
            await callback_query.message.edit_text(
                f"**Editando:** {revista['nombre']}\n\n"
                "Selecciona el campo a modificar:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await callback_query.answer()
            return

        if data.startswith("cp_field_"):
            # Formato: cp_field_<rev_id>_<campo>
            # Tanto rev_id (ej: KIKI_REV) como campo (ej: encryption_key) pueden
            # contener '_', así que buscamos el campo conocido desde el final.
            field = next((f for f in EDITABLE_FIELDS
                          if data.endswith(f"_{f}")), None)
            if not field:
                await callback_query.answer("Formato inválido", show_alert=True)
                return
            rev_id = data[len("cp_field_"):-(len(field) + 1)]
            set_admin_state(user_id, {
                "action": "edit_field",
                "rev_id": rev_id,
                "field": field,
            })
            await callback_query.message.edit_text(
                f"✏️ Envía el nuevo valor para **{field}**.\n\n"
                f"Para cancelar, escribe /cancel"
            )
            await callback_query.answer()
            return

        if data.startswith("cp_test_"):
            rev_id = data.replace("cp_test_", "")
            revista = await db.get_revista(rev_id)
            if not revista:
                await callback_query.answer("Revista no encontrada", show_alert=True)
                return
            await callback_query.answer("Probando conexión...")
            from uploader import RevistaUploader
            uploader = RevistaUploader(
                username=revista['username'],
                password=revista['password'],
                submission_id=revista['submission_id'],
                base_url=revista['base_url'],
                contexto=revista['contexto'],
                bitzero_mode=revista.get('bitzero_mode', 0),
                encryption_key=revista.get('encryption_key'),
            )
            ok = uploader.login()
            await db.update_revista_login_status(rev_id, ok)
            await callback_query.message.edit_text(
                f"✅ **Conexión exitosa.**" if ok
                else f"❌ **Falló la conexión.** Revisa credenciales."
            )
            return

        if data == "cp_back":
            revistas = await db.list_revistas()
            keyboard = []
            for r in revistas:
                keyboard.append([InlineKeyboardButton(
                    f"📚 {r['nombre']} (Modo {r.get('bitzero_mode', 0)})",
                    callback_data=f"cp_edit_{r['rev_id']}"
                )])
            keyboard.append([InlineKeyboardButton("❌ Cerrar", callback_data="cp_close")])
            await callback_query.message.edit_text(
                "🔧 **Panel de Control de Revistas**\n\n"
                "Selecciona una revista para editar sus parámetros.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await callback_query.answer()
            return

        if data == "cp_close":
            try:
                await callback_query.message.delete()
            except Exception:
                pass
            await callback_query.answer("Panel cerrado")
            return
