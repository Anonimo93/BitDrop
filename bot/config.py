"""
config.py — Configuración central del bot.

Lee variables de entorno, expone parámetros globales y bootstrap de la DB.
La configuración de revistas se carga desde la DB (no desde JSON) para
garantizar consistencia transaccional y concurrencia segura.
"""
from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)


class Config:
    """Configuración global cargada desde variables de entorno."""

    def __init__(self) -> None:
        # ── Credenciales Telegram (OBLIGATORIAS vía .env) ──────────────
        # NO dejar valores reales aquí: el bot no arranca sin ellas.
        self.API_ID: int = int(os.getenv("API_ID", "0") or 0)
        self.API_HASH: str = os.getenv("API_HASH", "")
        self.BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

        # ── Administradores iniciales (separados por coma) ────────────
        # Estos son los SUPER-ADMIN semilla. Otros admins se añaden vía
        # /addadmin y se guardan en la tabla `admins` de la DB.
        admin_ids_str = os.getenv("ADMIN_ID", "6438282268,6829735291")
        self.SEED_ADMIN_IDS: List[int] = [
            int(i.strip()) for i in admin_ids_str.split(",") if i.strip()
        ]

        # ── Directorios ────────────────────────────────────────────────
        self.ROOT_DIR: str = os.getenv("ROOT_DIR", "raiz")
        self.DATA_DIR: str = os.getenv("DATA_DIR", "data")
        self.LOGS_DIR: str = os.getenv("LOGS_DIR", "logs")

        # ── Base de datos ──────────────────────────────────────────────
        self.DB_PATH: str = os.path.join(self.DATA_DIR, "bot.db")

        # ── Chunking y descargas ───────────────────────────────────────
        self.CHUNK_SIZE_MB: int = int(os.getenv("CHUNK_SIZE_MB", "20"))
        self.DOWNLOAD_TIMEOUT: int = int(os.getenv("DOWNLOAD_TIMEOUT", "600"))
        self.MAX_DOWNLOAD_ATTEMPTS: int = int(os.getenv("MAX_DOWNLOAD_ATTEMPTS", "5"))
        self.DOWNLOAD_CHUNK_SIZE: int = int(os.getenv("DOWNLOAD_CHUNK_SIZE", "32768"))

        # ── Quotas por usuario ─────────────────────────────────────────
        self.DEFAULT_USER_QUOTA_MB: int = int(os.getenv("DEFAULT_USER_QUOTA_MB", "1024"))

        # ── BitZero ────────────────────────────────────────────────────
        self.BITZERO_SIGNATURE: str = os.getenv("BITZERO_SIG", "@bitzero#2024")
        self.BITZERO_FAKE_HOST: str = os.getenv("BITZERO_HOST", "https://bitzero.techdev.cu")

        # ── Acortador de URLs (URLs cortas) ────────────────────────────
        # Base pública donde se sirve el shortener (debe apuntar DNS al
        # servidor del bot). Ej: https://btz.dwn
        self.SHORT_URL_BASE: str = os.getenv("SHORT_URL_BASE", "https://btz.dwn")
        self.SHORTENER_HOST: str = os.getenv("SHORTENER_HOST", "0.0.0.0")
        self.SHORTENER_PORT: int = int(os.getenv("SHORTENER_PORT", "8080"))
        # TTL en días de los códigos cortos. 0 = sin expiración.
        self.SHORT_URL_TTL_DAYS: int = int(os.getenv("SHORT_URL_TTL_DAYS", "0"))

        # ── TLS ────────────────────────────────────────────────────────
        # Las revistas cubanas suelen usar certs self-signed, por eso el
        # default es NO verificar (comportamiento actual). Pon TLS_VERIFY=1
        # en el .env si tus revistas tienen certificados válidos.
        self.TLS_VERIFY: bool = os.getenv("TLS_VERIFY", "0") in ("1", "true", "True", "yes", "on")

        # ── Estado de admin (TTL) ──────────────────────────────────────
        self.ADMIN_STATE_TTL_SEC: int = int(os.getenv("ADMIN_STATE_TTL_SEC", "300"))

        # ── Identidad del bot ──────────────────────────────────────────
        self.DEVELOPER_HANDLE: str = os.getenv("DEVELOPER_HANDLE", "@Emanuel14APK")

        # ── Canales Telegram ───────────────────────────────────────────
        # Canal donde se LOG todo lo que se sube (texto/audit)
        # Formato: -1001234567890 (channel ID con -100 prefix)
        self.LOG_CHANNEL_ID: int = int(os.getenv("LOG_CHANNEL_ID", "0"))

        # Canal privado donde se REENVÍA todo archivo recibido por el bot,
        # para tener backup sin consumir megas del usuario (file_id ref).
        self.STORAGE_CHANNEL_ID: int = int(os.getenv("STORAGE_CHANNEL_ID", "0"))

        # ── Crear directorios base ─────────────────────────────────────
        self._ensure_dirs()

        # ── Cache en memoria de admins (se llena desde DB en startup) ──
        self._admins_cache: set[int] = set(self.SEED_ADMIN_IDS)

    def _ensure_dirs(self) -> None:
        for d in (self.ROOT_DIR, self.DATA_DIR, self.LOGS_DIR):
            os.makedirs(d, exist_ok=True)
        logger.info(f"Directorios inicializados: root={self.ROOT_DIR} data={self.DATA_DIR} logs={self.LOGS_DIR}")

    # ── Helpers de admins (cache en memoria + DB) ────────────────────
    def is_admin(self, user_id: int) -> bool:
        """Verifica si un user_id es admin (cache en memoria)."""
        return user_id in self._admins_cache

    def add_admin_to_cache(self, user_id: int) -> None:
        self._admins_cache.add(user_id)

    def remove_admin_from_cache(self, user_id: int) -> None:
        # Nunca remover los SUPER-ADMIN semilla
        if user_id in self.SEED_ADMIN_IDS:
            return
        self._admins_cache.discard(user_id)

    def get_all_admins(self) -> List[int]:
        return sorted(self._admins_cache)

    def validate(self) -> None:
        """Comprueba que las variables obligatorias estén presentes.
        Se llama en el arranque (main.py) antes de crear el cliente.
        """
        missing = []
        if not self.API_ID or self.API_ID <= 0:
            missing.append("API_ID")
        if not self.API_HASH:
            missing.append("API_HASH")
        if not self.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if missing:
            raise RuntimeError(
                "Faltan variables de entorno obligatorias: "
                + ", ".join(missing)
                + ". Copia .env.example a .env y complétalas."
            )


# Singleton global
config = Config()
