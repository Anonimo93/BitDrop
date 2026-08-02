"""
main.py — Entry point del Bit Uploader v2.

Inicializa:
  1. Logging estructurado con rotación.
  2. Base de datos SQLite asíncrona.
  3. Admins semilla (de .env) + admins dinámicos (de DB).
  4. Revistas por defecto (si la DB está vacía).
  5. Cliente Pyrogram y registro de handlers.
  6. Pre-login de revistas para detectar credenciales malas temprano.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys

from config import config
from database import db

# ── Logging estructurado con rotación ───────────────────────────────
os.makedirs(config.LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            os.path.join(config.LOGS_DIR, 'bot.log'),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding='utf-8'
        ),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


async def seed_admins() -> None:
    """Asegura que todos los ADMIN_ID (semilla) estén en la tabla admins."""
    for admin_id in config.SEED_ADMIN_IDS:
        # Insertar como seed si no existe
        await db.add_admin(admin_id, username=None, added_by=admin_id, is_seed=True)
    # Cargar todos los admins (semilla + dinámicos) al cache
    await db.load_admins_to_cache()
    logger.info(f"👑 Admins activos: {config.get_all_admins()}")


async def seed_default_revistas() -> None:
    """Si la DB no tiene revistas, inserta las 4 por defecto desde env vars."""
    existing = await db.list_revistas()
    if existing:
        logger.info(f"DB ya tiene {len(existing)} revistas, saltando seed.")
        return

    defaults = [
        ("KIKI_REV", "Revista Cardiología", "https://revcardiologia.sld.cu",
         "revcardiologia", os.getenv("KIKI_USER", ""),
         os.getenv("KIKI_PASS", ""), os.getenv("KIKI_SUB", ""),
         int(os.getenv("KIKI_BITZERO", "1")), os.getenv("KIKI_KEY", "default_key_1")),
        ("COMED_REV", "Revista COMED", "https://revcocmed.sld.cu",
         "cocmed", os.getenv("COMED_USER", ""),
         os.getenv("COMED_PASS", ""), os.getenv("COMED_SUB", ""),
         int(os.getenv("COMED_BITZERO", "1")), os.getenv("COMED_KEY", "default_key_2")),
        ("EMS_REV", "Revista EMS", "https://ems.sld.cu",
         "ems", os.getenv("EMS_USER", ""),
         os.getenv("EMS_PASS", ""), os.getenv("EMS_SUB", ""),
         int(os.getenv("EMS_BITZERO", "1")), os.getenv("EMS_KEY", "default_key_3")),
        ("RUS_REV", "Revista RUS", "https://rus.ucf.edu.cu",
         "rus", os.getenv("RUS_USER", ""),
         os.getenv("RUS_PASS", ""), os.getenv("RUS_SUB", ""),
         int(os.getenv("RUS_BITZERO", "1")), os.getenv("RUS_KEY", "default_key_4")),
    ]
    seeded = 0
    for (rev_id, nombre, base_url, contexto, user, pwd, sub, bz, key) in defaults:
        if not (user and pwd and sub):
            logger.warning(f"Saltando seed de {rev_id}: faltan credenciales "
                           f"({rev_id[:4]}_USER/PASS/SUB) en el .env")
            continue
        await db.upsert_revista(
            rev_id, nombre=nombre, base_url=base_url, contexto=contexto,
            username=user, password=pwd, submission_id=sub,
            bitzero_mode=bz, encryption_key=key, active=1
        )
        seeded += 1
    logger.info(f"Seed completado: {seeded}/{len(defaults)} revistas insertadas.")


async def prelogin_revistas() -> None:
    """Intenta login en cada revista para detectar fallos temprano."""
    from uploader import RevistaUploader
    revistas = await db.list_revistas(only_active=True)
    logger.info(f"Pre-login de {len(revistas)} revistas...")

    loop = asyncio.get_running_loop()
    for r in revistas:
        uploader = RevistaUploader(
            username=r['username'], password=r['password'],
            submission_id=r['submission_id'], base_url=r['base_url'],
            contexto=r['contexto'], bitzero_mode=r.get('bitzero_mode', 0),
            encryption_key=r.get('encryption_key'),
        )
        ok = await loop.run_in_executor(None, uploader.login)
        await db.update_revista_login_status(r['rev_id'], ok)
        if ok:
            logger.info(f"  ✅ {r['nombre']}: login OK")
        else:
            logger.warning(f"  ❌ {r['nombre']}: login fallido")


async def main_async() -> None:
    logger.info("=" * 60)
    logger.info("🚀 Iniciando Bit Uploader v2 (modular)")
    logger.info("=" * 60)

    # 0. Validar configuración obligatoria (API_ID, API_HASH, BOT_TOKEN)
    try:
        config.validate()
    except RuntimeError as e:
        logger.error(f"Configuración inválida: {e}")
        raise

    # 1. Inicializar DB
    await db.init()

    # 2. Seed de admins (semilla + dinámicos) y revistas
    await seed_admins()
    await seed_default_revistas()

    # 3. Pyrogram client
    from pyrogram import Client
    app = Client(
        "revista_bot_v2",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
        workdir=config.DATA_DIR,
    )

    # 4. Registrar handlers
    from handlers.commands import register as reg_commands
    from handlers.admin import register as reg_admin
    from handlers.files import register as reg_files
    from handlers.callbacks import register as reg_callbacks

    reg_commands(app)
    reg_admin(app)
    reg_files(app)
    reg_callbacks(app)
    logger.info("Handlers registrados: commands, admin, files, callbacks")

    # 5. Pre-login (no bloquear arranque)
    asyncio.create_task(prelogin_revistas())

    # 6. Servidor de URLs cortas (aiohttp, no bloquea el loop)
    from shortener import run_shortener_server
    asyncio.create_task(run_shortener_server())

    # 6. Stats
    stats = await db.get_global_stats()
    logger.info(f"📊 Stats: {stats['active_users']} usuarios activos, "
                f"{stats['total_admins']} admins, "
                f"{stats['active_revistas']} revistas activas")
    if config.LOG_CHANNEL_ID:
        logger.info(f"📝 Canal de LOG: {config.LOG_CHANNEL_ID}")
    if config.STORAGE_CHANNEL_ID:
        logger.info(f"📦 Canal de almacenamiento: {config.STORAGE_CHANNEL_ID}")

    # 7. Run
    logger.info("✅ Bot listo. Esperando comandos...")
    logger.info("=" * 60)
    await app.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.stop()
        await db.close()
        logger.info("Bot detenido.")


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Interrumpido por usuario.")
    except Exception as e:
        logger.exception(f"Error fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
