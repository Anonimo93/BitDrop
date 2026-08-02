"""
database.py — Base de datos SQL asíncrona (aiosqlite).

Centraliza toda la persistencia:
  - users       (autorizaciones + quotas)
  - admins      (administradores dinámicos añadidos vía /addadmin)
  - files       (registro de archivos recibidos)
  - uploads     (historial de subidas BitZero)
  - revistas    (configuración de revistas OJS)
  - ojs_sessions(cookies + CSRF persistidos por revista)

El acceso es 100% asíncrono y seguro para concurrencia (sqlite3 maneja
locking interno con WAL activado).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from config import config

logger = logging.getLogger(__name__)


class AsyncDB:
    """Wrapper sobre aiosqlite con API de alto nivel."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── Lifecycle ─────────────────────────────────────────────────────
    async def init(self) -> None:
        """Abre conexión, activa WAL y crea tablas si no existen."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._create_schema()
        await self._conn.commit()
        logger.info(f"DB inicializada en {self.db_path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _create_schema(self) -> None:
        assert self._conn is not None
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id        INTEGER PRIMARY KEY,
                username       TEXT,
                added_by       INTEGER,
                added_date     TEXT NOT NULL,
                active         INTEGER NOT NULL DEFAULT 1,
                last_access    TEXT,
                quota_mb       INTEGER NOT NULL DEFAULT 1024,
                is_admin       INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);

            -- ─── Tabla de administradores dinámicos ───────────────────
            CREATE TABLE IF NOT EXISTS admins (
                user_id        INTEGER PRIMARY KEY,
                username       TEXT,
                added_by       INTEGER,
                added_date     TEXT NOT NULL,
                is_seed        INTEGER NOT NULL DEFAULT 0  -- 1 si viene de ADMIN_ID del .env
            );

            CREATE TABLE IF NOT EXISTS files (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                file_name      TEXT NOT NULL,
                file_path      TEXT NOT NULL,
                file_size      INTEGER NOT NULL,
                mime_type      TEXT,
                received_date  TEXT NOT NULL,
                tg_message_id  INTEGER,
                storage_msg_id INTEGER,  -- ID del mensaje en el canal de almacenamiento
                storage_channel_id INTEGER,  -- Canal donde se respaldó
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id);
            CREATE INDEX IF NOT EXISTS idx_files_date ON files(received_date);

            CREATE TABLE IF NOT EXISTS uploads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                revista_id      TEXT NOT NULL,
                submission_id   TEXT NOT NULL,
                original_name   TEXT NOT NULL,
                original_size   INTEGER NOT NULL,
                uploaded_size   INTEGER NOT NULL,
                file_ids        TEXT NOT NULL,
                bitzero_mode    INTEGER NOT NULL,
                bitzero_url     TEXT,
                encryption_key  TEXT,
                status          TEXT NOT NULL,
                uploaded_at     TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id);
            CREATE INDEX IF NOT EXISTS idx_uploads_revista ON uploads(revista_id);

            CREATE TABLE IF NOT EXISTS revistas (
                rev_id          TEXT PRIMARY KEY,
                nombre          TEXT NOT NULL,
                base_url        TEXT NOT NULL,
                contexto        TEXT NOT NULL,
                username        TEXT NOT NULL,
                password        TEXT NOT NULL,
                submission_id   TEXT NOT NULL,
                bitzero_mode    INTEGER NOT NULL DEFAULT 0,
                encryption_key  TEXT,
                active          INTEGER NOT NULL DEFAULT 1,
                last_login_at   TEXT,
                last_login_ok   INTEGER
            );

            CREATE TABLE IF NOT EXISTS ojs_sessions (
                revista_id      TEXT PRIMARY KEY,
                cookies_json    TEXT NOT NULL,
                csrf_token      TEXT,
                created_at      TEXT NOT NULL,
                last_used_at    TEXT NOT NULL,
                FOREIGN KEY (revista_id) REFERENCES revistas(rev_id) ON DELETE CASCADE
            );

            -- ─── Acortador de URLs (código corto → URL BitZero completa) ──
            -- Sin FOREIGN KEY a users: el usuario puede no existir aún en la
            -- tabla users (p.ej. borrado) y no queremos bloquear la URL.
            CREATE TABLE IF NOT EXISTS short_urls (
                code         TEXT PRIMARY KEY,
                full_url     TEXT NOT NULL,
                user_id      INTEGER,
                created_at   TEXT NOT NULL,
                expires_at   TEXT  -- NULL = sin expiración
            );
            CREATE INDEX IF NOT EXISTS idx_short_urls_user ON short_urls(user_id);
        """)

    # ── Admins (dinámicos) ────────────────────────────────────────────
    async def add_admin(self, user_id: int, username: Optional[str],
                        added_by: int, is_seed: bool = False) -> bool:
        """Añade un admin. Devuelve True si se insertó, False si ya existía."""
        assert self._conn is not None
        try:
            await self._conn.execute(
                """INSERT INTO admins (user_id, username, added_by, added_date, is_seed)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, username, added_by,
                 time.strftime('%Y-%m-%d %H:%M:%S'), 1 if is_seed else 0)
            )
            await self._conn.commit()
            config.add_admin_to_cache(user_id)
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_admin(self, user_id: int) -> bool:
        """Quita un admin. Devuelve True si se eliminó.
        Los SEED admins (de .env) no se pueden quitar por DB.
        """
        assert self._conn is not None
        # Verificar que no sea seed
        async with self._conn.execute(
            "SELECT is_seed FROM admins WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row and row['is_seed'] == 1:
                return False
        cur = await self._conn.execute(
            "DELETE FROM admins WHERE user_id = ? AND is_seed = 0",
            (user_id,)
        )
        await self._conn.commit()
        if cur.rowcount > 0:
            config.remove_admin_from_cache(user_id)
            return True
        return False

    async def list_admins(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM admins ORDER BY added_date ASC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def is_admin_in_db(self, user_id: int) -> bool:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def load_admins_to_cache(self) -> None:
        """Carga todos los admins desde DB al cache en memoria.
        Se llama en startup.
        """
        admins = await self.list_admins()
        for a in admins:
            config.add_admin_to_cache(a['user_id'])
        # Los seeds siempre se mantienen
        for seed_id in config.SEED_ADMIN_IDS:
            config.add_admin_to_cache(seed_id)
        logger.info(f"Admins cargados en cache: {len(config.get_all_admins())} total")

    # ── Users ─────────────────────────────────────────────────────────
    async def add_user(self, user_id: int, username: Optional[str], added_by: int,
                       quota_mb: Optional[int] = None) -> bool:
        """Devuelve True si se insertó, False si ya existía."""
        assert self._conn is not None
        quota = quota_mb if quota_mb is not None else config.DEFAULT_USER_QUOTA_MB
        is_admin = 1 if config.is_admin(user_id) else 0
        try:
            await self._conn.execute(
                """INSERT INTO users (user_id, username, added_by, added_date, active, quota_mb, is_admin)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (user_id, username, added_by, time.strftime('%Y-%m-%d %H:%M:%S'), quota, is_admin)
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_user(self, user_id: int) -> bool:
        assert self._conn is not None
        # No permitir eliminar a admins
        if config.is_admin(user_id):
            return False
        cur = await self._conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def is_authorized(self, user_id: int) -> bool:
        """Admins siempre autorizados; resto debe estar activo en DB."""
        if config.is_admin(user_id):
            return True
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT 1 FROM users WHERE user_id = ? AND active = 1", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_users(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute("SELECT * FROM users ORDER BY added_date DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_last_access(self, user_id: int) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE users SET last_access = ? WHERE user_id = ?",
            (time.strftime('%Y-%m-%d %H:%M:%S'), user_id)
        )
        await self._conn.commit()

    async def set_user_quota(self, user_id: int, quota_mb: int) -> bool:
        assert self._conn is not None
        cur = await self._conn.execute(
            "UPDATE users SET quota_mb = ? WHERE user_id = ?",
            (quota_mb, user_id)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    # ── Files ─────────────────────────────────────────────────────────
    async def register_file(self, user_id: int, file_name: str, file_path: str,
                            file_size: int, mime_type: Optional[str],
                            tg_message_id: Optional[int],
                            storage_msg_id: Optional[int] = None,
                            storage_channel_id: Optional[int] = None) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO files (user_id, file_name, file_path, file_size, mime_type,
                                  received_date, tg_message_id, storage_msg_id, storage_channel_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, file_name, file_path, file_size, mime_type,
             time.strftime('%Y-%m-%d %H:%M:%S'), tg_message_id,
             storage_msg_id, storage_channel_id)
        )
        await self._conn.commit()
        return cur.lastrowid or 0

    async def list_user_files(self, user_id: int) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM files WHERE user_id = ? ORDER BY received_date DESC",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def delete_file(self, file_id: int, user_id: int) -> bool:
        """Borra sólo si pertenece al usuario (aislamiento)."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "DELETE FROM files WHERE id = ? AND user_id = ?",
            (file_id, user_id)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_user_usage(self, user_id: int) -> int:
        """Suma bytes usados por el usuario."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT COALESCE(SUM(file_size), 0) AS total FROM files WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row['total']) if row else 0

    # ── Uploads (historial) ───────────────────────────────────────────
    async def log_upload(self, user_id: int, revista_id: str, submission_id: str,
                         original_name: str, original_size: int, uploaded_size: int,
                         file_ids: List[str], bitzero_mode: int,
                         bitzero_url: Optional[str], encryption_key: Optional[str],
                         status: str) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO uploads
               (user_id, revista_id, submission_id, original_name, original_size,
                uploaded_size, file_ids, bitzero_mode, bitzero_url, encryption_key,
                status, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, revista_id, submission_id, original_name, original_size,
             uploaded_size, json.dumps(file_ids), bitzero_mode, bitzero_url,
             encryption_key, status, time.strftime('%Y-%m-%d %H:%M:%S'))
        )
        await self._conn.commit()
        return cur.lastrowid or 0

    async def list_user_uploads(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM uploads WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            rows = await cur.fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d['file_ids'] = json.loads(d['file_ids']) if d['file_ids'] else []
                results.append(d)
            return results

    # ── Revistas ──────────────────────────────────────────────────────
    async def get_revista(self, rev_id: str) -> Optional[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM revistas WHERE rev_id = ?", (rev_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_revistas(self, only_active: bool = False) -> List[Dict[str, Any]]:
        assert self._conn is not None
        sql = "SELECT * FROM revistas"
        if only_active:
            sql += " WHERE active = 1"
        sql += " ORDER BY nombre"
        async with self._conn.execute(sql) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def upsert_revista(self, rev_id: str, **fields: Any) -> bool:
        """Inserta o actualiza una revista."""
        assert self._conn is not None
        allowed = {'nombre', 'base_url', 'contexto', 'username', 'password',
                   'submission_id', 'bitzero_mode', 'encryption_key', 'active'}
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return False

        cols = ['rev_id'] + list(clean.keys())
        placeholders = ', '.join('?' * len(cols))
        values = [rev_id] + list(clean.values())
        updates = ', '.join(f"{c}=excluded.{c}" for c in clean.keys())
        sql = f"""
            INSERT INTO revistas ({', '.join(cols)}) VALUES ({placeholders})
            ON CONFLICT(rev_id) DO UPDATE SET {updates}
        """
        cur = await self._conn.execute(sql, values)
        await self._conn.commit()
        return cur.rowcount > 0

    async def update_revista_field(self, rev_id: str, field: str, value: Any) -> bool:
        """Actualiza un campo concreto (con whitelist por seguridad)."""
        allowed = {'nombre', 'base_url', 'contexto', 'username', 'password',
                   'submission_id', 'bitzero_mode', 'encryption_key', 'active'}
        if field not in allowed:
            return False
        assert self._conn is not None
        cur = await self._conn.execute(
            f"UPDATE revistas SET {field} = ? WHERE rev_id = ?",
            (value, rev_id)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def update_revista_login_status(self, rev_id: str, ok: bool) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE revistas SET last_login_at = ?, last_login_ok = ? WHERE rev_id = ?",
            (time.strftime('%Y-%m-%d %H:%M:%S'), 1 if ok else 0, rev_id)
        )
        await self._conn.commit()

    # ── OJS Sessions ──────────────────────────────────────────────────
    async def save_ojs_session(self, revista_id: str, cookies_json: str,
                               csrf_token: Optional[str]) -> None:
        assert self._conn is not None
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        await self._conn.execute(
            """INSERT INTO ojs_sessions (revista_id, cookies_json, csrf_token, created_at, last_used_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(revista_id) DO UPDATE SET
                   cookies_json = excluded.cookies_json,
                   csrf_token   = excluded.csrf_token,
                   last_used_at = excluded.last_used_at""",
            (revista_id, cookies_json, csrf_token, now, now)
        )
        await self._conn.commit()

    async def load_ojs_session(self, revista_id: str) -> Optional[Dict[str, Any]]:
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM ojs_sessions WHERE revista_id = ?", (revista_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Short URLs (acortador) ────────────────────────────────────────
    async def create_short_url(self, code: str, full_url: str,
                               user_id: Optional[int],
                               expires_at: Optional[str] = None) -> bool:
        """Registra un código corto. Devuelve True si se insertó
        (False si el código ya existe)."""
        assert self._conn is not None
        try:
            await self._conn.execute(
                """INSERT INTO short_urls (code, full_url, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (code, full_url, user_id,
                 time.strftime('%Y-%m-%d %H:%M:%S'), expires_at)
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_short_url(self, code: str) -> Optional[Dict[str, Any]]:
        """Devuelve la fila del código corto o None."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM short_urls WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def find_short_url_by_full_url(self, full_url: str) -> Optional[Dict[str, Any]]:
        """Devuelve un código existente para la misma URL (evita duplicados)."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT * FROM short_urls WHERE full_url = ? LIMIT 1", (full_url,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def delete_short_url(self, code: str) -> bool:
        assert self._conn is not None
        cur = await self._conn.execute(
            "DELETE FROM short_urls WHERE code = ?", (code,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def prune_expired_short_urls(self) -> int:
        """Elimina códigos cortos expirados. Devuelve cuántos se borraron."""
        assert self._conn is not None
        cur = await self._conn.execute(
            "DELETE FROM short_urls WHERE expires_at IS NOT NULL AND expires_at < ?",
            (time.strftime('%Y-%m-%d %H:%M:%S'),)
        )
        await self._conn.commit()
        return cur.rowcount or 0

    # ── Stats ─────────────────────────────────────────────────────────
    async def get_global_stats(self) -> Dict[str, Any]:
        assert self._conn is not None
        stats: Dict[str, Any] = {}
        async with self._conn.execute("SELECT COUNT(*) AS n FROM users WHERE active = 1") as cur:
            stats['active_users'] = (await cur.fetchone())['n']
        async with self._conn.execute("SELECT COUNT(*) AS n FROM users") as cur:
            stats['total_users'] = (await cur.fetchone())['n']
        async with self._conn.execute("SELECT COUNT(*) AS n FROM admins") as cur:
            stats['total_admins'] = (await cur.fetchone())['n']
        async with self._conn.execute("SELECT COUNT(*) AS n FROM files") as cur:
            stats['total_files'] = (await cur.fetchone())['n']
        async with self._conn.execute("SELECT COALESCE(SUM(file_size), 0) AS s FROM files") as cur:
            stats['total_bytes'] = (await cur.fetchone())['s']
        async with self._conn.execute("SELECT COUNT(*) AS n FROM uploads WHERE status = 'success'") as cur:
            stats['successful_uploads'] = (await cur.fetchone())['n']
        async with self._conn.execute("SELECT COUNT(*) AS n FROM revistas WHERE active = 1") as cur:
            stats['active_revistas'] = (await cur.fetchone())['n']
        return stats


# Singleton global
db = AsyncDB(config.DB_PATH)
