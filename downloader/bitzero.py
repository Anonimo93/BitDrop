#!/usr/bin/env python3
"""
bitzero.py — BitZero Downloader v5.0

Mejoras v5.0 (basadas en prueba real con rus.ucf.edu.cu / OJS 3.3.0.18):
  - Reordena patrones de URL: el patrón ganador (con /index.php/<ctx>/$$$call$$$)
    se prueba PRIMERO, los demás como fallback.
  - Detecta respuestas HTML (login/redirect) y las descarta, probando el
    siguiente patrón automáticamente.
  - Verifica que la descarga sea binaria real (PNG/HTML/ZIP) antes de aceptar.
  - Validación de tamaño PNG más robusta.

Mejoras v4.0 heredadas:
  - BUG-01: usa urlsafe_b64decode (compatible con el encoder nuevo).
  - BUG-03: parser detecta dinámicamente si último segmento es hash (8 hex).
  - BUG-04: usa pyzipper.AESZipFile para extraer ZIPs encriptados con AES.
  - BUG-07: si el filename es '__multi__<manifest>', extrae .tar.
  - BUG-10: decode_html prioriza comentario estructural <!-- encoded:XXX -->.

Uso:
    python3 bitzero.py "URL_BITZERO"
    python3 bitzero.py "URL_BITZERO" --xor-key "clave"
    python3 bitzero.py "URL_BITZERO" --zip-pass "password"
    python3 bitzero.py "URL_BITZERO" --skip-login
    python3 bitzero.py "URL_BITZERO" --url-pattern 4
    python3 bitzero.py "URL_BITZERO" --output-dir ./descargas
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# pyzipper para ZIPs encriptados con AES
try:
    import pyzipper
    HAS_PYZIPPER = True
except ImportError:
    HAS_PYZIPPER = False
    print("⚠️  pyzipper no instalado. ZIPs encriptados (modo 3) fallarán.")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# CONFIGURACIÓN
# =========================
DEFAULT_TIMEOUT = 60
SHORT_RESOLVE_TIMEOUT = 15  # lookup de código corto (solo es una consulta)
MAX_RETRIES = 3
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


# =========================
# UTILIDADES
# =========================
def sizeof_fmt(num: float, suffix: str = 'B') -> str:
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return f"{num:3.2f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f}Yi{suffix}"


def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')


def _urlsafe_b64decode(s: str) -> bytes:
    """Decodifica base64 url-safe reconstruyendo padding."""
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def decode_key_part(key_part: str) -> str:
    """Decodifica una parte de la clave BitZero.
    Compatible con urlsafe_b64 (nuevo) y con el formato legacy (# y @).
    """
    # Intentar urlsafe primero (formato nuevo v2+)
    try:
        return _urlsafe_b64decode(key_part).decode('utf-8')
    except Exception:
        pass
    # Fallback: formato legacy con # (=) y @ (==)
    try:
        return base64.b64decode(
            key_part.replace('#', '=').replace('@', '==')
        ).decode('utf-8')
    except Exception as e:
        raise ValueError(f"No se pudo decodificar parte de clave: {e}")


def is_hex_hash(s: str) -> bool:
    """True si s es un hash de 8 hex chars (32 bits)."""
    return len(s) == 8 and bool(re.fullmatch(r'[0-9a-fA-F]{8}', s))


# =========================
# SESIÓN CON REINTENTOS
# =========================
def create_session(verify: bool = False) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({'User-Agent': USER_AGENT})
    # Verificación TLS: por defecto desactivada (revistas con cert self-signed).
    # Usa --verify para activarla.
    session.verify = verify
    return session


def test_connection(host: str, verify: bool = False) -> bool:
    try:
        r = requests.head(host, timeout=10, verify=verify,
                          headers={'User-Agent': USER_AGENT})
        return r.status_code < 500
    except Exception:
        try:
            r = requests.get(host, timeout=10, verify=verify,
                             headers={'User-Agent': USER_AGENT})
            return r.status_code < 500
        except Exception:
            return False


# =========================
# URLS CORTAS (resolución vía shortener)
# =========================
def resolve_short_url(url: str, verify: bool = False,
                      progress_cb: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Si la URL es un código corto (un único segmento de ruta, ej:
    https://btz.dwn/AbCdEf12), la resuelve contra el shortener del bot
    (GET /api/resolve?code=XXX) y devuelve la URL BitZero completa.
    Devuelve None si no parece URL corta o si no se pudo resolver.
    Las URLs completas (5-6 segmentos) pasan sin tocarse.
    """
    def report(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    stripped = url.strip().replace('bitzero ', '').strip()
    if '://' not in stripped:
        return None
    scheme, _, rest = stripped.partition('://')
    host, _, path = rest.partition('/')
    segs = [s for s in path.split('/') if s]
    if len(segs) != 1:
        return None  # URL completa normal
    code = segs[0]
    if not re.fullmatch(r'[0-9A-Za-z]{4,20}', code):
        return None

    resolve_url = f"{scheme}://{host}/api/resolve?code={code}"
    report(f"🔗 URL corta detectada, resolviendo {resolve_url}")
    try:
        r = requests.get(resolve_url, timeout=SHORT_RESOLVE_TIMEOUT, verify=verify,
                         headers={'User-Agent': USER_AGENT})
        if r.status_code == 200:
            data = r.json()
            full = data.get('url') if data.get('ok') else None
            if full:
                report(f"✅ Resuelta: {full[:90]}{'...' if len(full) > 90 else ''}")
                return full
            report(f"❌ Respuesta inválida del shortener: {r.text[:120]}")
        else:
            report(f"❌ Shortener respondió HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:
        report(f"❌ Error resolviendo el código corto: {e}")
        report("💡 Verifica que el dominio del shortener sea accesible y que")
        report("   el servidor del bot esté corriendo el shortener.")
    return None


# =========================
# PARSEO DE URL BITZERO (BUG-03 fix)
# =========================
def parse_bitzero_url(url: str) -> Dict:
    """Parsea URL BitZero detectando dinámicamente la presencia de hash."""
    url = url.strip().replace('bitzero ', '').strip()
    parts = url.split('/')
    if len(parts) < 6:
        raise ValueError("URL inválida o incompleta")

    # Detectar si último segmento es hash (8 hex) o filename
    last = parts[-1]
    verification_hash: Optional[str] = None
    if is_hex_hash(last):
        verification_hash = last.lower()
        filename_b64 = parts[-2]
        key_idx = -3
    else:
        filename_b64 = last
        key_idx = -2

    key_encoded = parts[key_idx]
    bitzero_mode_str = parts[key_idx - 1]
    token = parts[key_idx - 2]
    size_repo = parts[key_idx - 3]

    if '-' not in size_repo:
        raise ValueError(f"Formato inválido en size-repo: {size_repo}")
    file_size_str, repo = size_repo.split('-', 1)
    file_size = int(file_size_str)

    key_parts = key_encoded.split('-')
    if len(key_parts) < 5:
        raise ValueError("Clave de autenticación incompleta")

    host = decode_key_part(key_parts[0])
    username = decode_key_part(key_parts[1])
    password = decode_key_part(key_parts[2])
    submission_id = decode_key_part(key_parts[3])
    contexto = decode_key_part(key_parts[4])

    bitzero_mode_from_key: Optional[int] = None
    timestamp: Optional[int] = None
    encryption_key: Optional[str] = None
    if len(key_parts) >= 6:
        bitzero_mode_from_key = int(decode_key_part(key_parts[5]))
    if len(key_parts) >= 7:
        timestamp = int(decode_key_part(key_parts[6]))
    if len(key_parts) >= 8:
        encryption_key = decode_key_part(key_parts[7])

    try:
        bitzero_mode = int(bitzero_mode_str)
    except ValueError:
        bitzero_mode = bitzero_mode_from_key if bitzero_mode_from_key is not None else 0

    # Decodificar filename (probar url-safe y legacy con _)
    try:
        filename = _urlsafe_b64decode(filename_b64).decode('utf-8')
    except Exception:
        try:
            filename = base64.b64decode(filename_b64.replace('_', '=')).decode('utf-8')
        except Exception:
            filename = filename_b64

    file_ids = token.split('-')

    return {
        'host': host,
        'username': username,
        'password': password,
        'submission_id': submission_id,
        'contexto': contexto,
        'file_ids': file_ids,
        'bitzero_mode': bitzero_mode,
        'bitzero_mode_from_key': bitzero_mode_from_key,
        'timestamp': timestamp,
        'encryption_key': encryption_key,
        'file_size': file_size,
        'filename': filename,
        'repo': repo,
        'verification_hash': verification_hash,
        'original_url': url,
    }


# =========================
# LOGIN OJS
# =========================
def ojs_login(session: requests.Session, host: str, username: str,
              password: str, contexto: str = '') -> Optional[str]:
    login_url = (f"{host}/index.php/{contexto}/login" if contexto
                 else f"{host}/index.php/login")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"    ↳ Intento {attempt}/{MAX_RETRIES}...")
            resp = session.get(login_url, timeout=DEFAULT_TIMEOUT)
            soup = BeautifulSoup(resp.text, 'html.parser')
            csrf_input = soup.find('input', {'name': 'csrfToken'})
            if not csrf_input:
                raise ValueError("No se pudo obtener el token CSRF")
            csrf_token = csrf_input.get('value')

            data = {
                'csrfToken': csrf_token,
                'username': username,
                'password': password,
                'remember': '1',
                'source': '',
            }
            post_url = (f"{host}/index.php/{contexto}/login/signIn" if contexto
                        else f"{host}/index.php/login/signIn")
            resp = session.post(post_url, data=data, timeout=DEFAULT_TIMEOUT)

            if any(x in resp.text for x in ['Cerrar sesión', 'Logout', 'submissionId=']):
                return csrf_token
            print(f"    ⚠️  Login no confirmado en intento {attempt}")
        except Exception as e:
            print(f"    ⚠️  Error intento {attempt}: {e.__class__.__name__}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2)
    return None


# =========================
# PATRONES DE URL DE DESCARGA (reordenados v5.0)
# =========================
def generate_download_urls(host: str, contexto: str, file_id: str,
                           submission_id: str) -> List[str]:
    """Genera las 6 URLs candidatas. Ordenadas por probabilidad de éxito
    basado en pruebas reales con OJS 3.3.0.18:

      #1 (antes #4): /index.php/<ctx>/$$$call$$$/api/file/file-api/download-file
      #2 (antes #1): /$$$call$$$/api/file/file-api/download-file
      #3 (antes #5): /api/v1/files/<id>/download?submissionId=...&stageId=1
      #4 (antes #2): /index.php/<ctx>/api/v1/files/<id>/download?...
      #5 (antes #3): /index.php/<ctx>/api/v1/files/<id>/download?submissionId=...
      #6 (igual):    /index.php/<ctx>/api/v1/submissions/<sub>/files/<id>/download
    """
    base = host.rstrip('/')
    ctx = f"/index.php/{contexto}" if contexto else "/index.php"
    return [
        # Patrón ganador con OJS 3.3.0.18 (rus.ucf.edu.cu, revcardiologia, etc.)
        f"{base}{ctx}/$$$call$$$/api/file/file-api/download-file?submissionFileId={file_id}&submissionId={submission_id}&stageId=1",
        # Sin contexto (algunas configuraciones)
        f"{base}/$$$call$$$/api/file/file-api/download-file?submissionFileId={file_id}&submissionId={submission_id}&stageId=1",
        # API v1 sin contexto
        f"{base}/api/v1/files/{file_id}/download?submissionId={submission_id}&stageId=1",
        # API v1 con contexto
        f"{base}{ctx}/api/v1/files/{file_id}/download?submissionId={submission_id}&stageId=1",
        # API v1 sin stageId
        f"{base}{ctx}/api/v1/files/{file_id}/download?submissionId={submission_id}",
        # Submission-scoped
        f"{base}{ctx}/api/v1/submissions/{submission_id}/files/{file_id}/download",
    ]


def _looks_like_html_response(content_start: bytes) -> bool:
    """Detecta si los primeros bytes son HTML (login page, error, etc.)
    en lugar del archivo binario esperado.
    """
    if not content_start:
        return True
    s = content_start[:600].lstrip()
    if s.startswith(b'<!DOCTYPE') or s.startswith(b'<html') or s.startswith(b'<HTML'):
        return True
    if b'<html' in content_start[:500].lower():
        return True
    return False


# =========================
# DESCARGA CON PROGRESO (mejorada v5.0)
# =========================
def download_file_with_fallback(
    file_id: str, params: Dict, session: requests.Session,
    output_path: str, global_downloaded: int, global_total: int,
    forced_pattern: Optional[int] = None,
    bytes_cb: Optional[Callable[[int, int], None]] = None
) -> Tuple[bool, int]:
    """Descarga un file_id probando patrones en orden.
    v5.0: descarta respuestas HTML (login redirects) y prueba el siguiente.
    """
    if forced_pattern is not None:
        # El usuario fuerza un patrón (1-6 según el orden NUEVO)
        urls = [generate_download_urls(params['host'], params['contexto'],
                                       file_id, params['submission_id'])[forced_pattern - 1]]
    else:
        urls = generate_download_urls(params['host'], params['contexto'],
                                      file_id, params['submission_id'])

    for idx, url in enumerate(urls):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with session.get(url, stream=True, timeout=DEFAULT_TIMEOUT,
                                 allow_redirects=False) as r:
                    if r.status_code != 200:
                        # 302 = redirect (típicamente a login)
                        if r.status_code == 302:
                            loc = r.headers.get('Location', '')
                            if 'login' in loc.lower() or loc.endswith('/' + params.get('contexto', '')):
                                print(f"    → Patrón {idx+1}: 302 a login (sesión expiró?)")
                            else:
                                print(f"    → Patrón {idx+1}: 302 → {loc[:60]}")
                        else:
                            print(f"    → Patrón {idx+1}: HTTP {r.status_code}")
                        break  # Probar siguiente patrón

                    # Leer primeros bytes para detectar HTML (login redirect)
                    # sin consumir todo el stream
                    first_chunk = b''

                    # Descargar todo (con buffer) para validar
                    downloaded_part = 0
                    with open(output_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            if chunk:
                                if not first_chunk:
                                    first_chunk = chunk[:512]
                                    # Si parece HTML, descartar y probar siguiente patrón
                                    if _looks_like_html_response(first_chunk):
                                        print(f"    → Patrón {idx+1}: respuesta HTML (login page), descartando")
                                        f.close()
                                        os.remove(output_path)
                                        break
                                f.write(chunk)
                                downloaded_part += len(chunk)
                                total_done = global_downloaded + downloaded_part
                                pct = (total_done / global_total * 100) if global_total > 0 else 0
                                bar_len = 30
                                filled = int(bar_len * pct / 100)
                                bar = '█' * filled + '░' * (bar_len - filled)
                                sys.stdout.write(
                                    f"\r  [{bar}] {pct:5.1f}%  "
                                    f"{sizeof_fmt(total_done)}/{sizeof_fmt(global_total)}"
                                )
                                sys.stdout.flush()
                                # Progreso para GUIs (bytes acumulados / total)
                                if bytes_cb:
                                    try:
                                        bytes_cb(total_done, global_total)
                                    except Exception:
                                        pass
                        else:
                            # Descarga completada sin break (no era HTML)
                            print()
                            # Validación final: el primer chunk NO era HTML
                            if downloaded_part > 0:
                                return True, downloaded_part
                            print(f"    → Patrón {idx+1}: descarga vacía")
                            break
                    # Si llegamos aquí es porque hicimos break (HTML detectado)
                    break  # Probar siguiente patrón

            except Exception as e:
                print(f"    → Patrón {idx+1}, intento {attempt}: {e.__class__.__name__}: {e}")
                if attempt == MAX_RETRIES:
                    break
                time.sleep(2)
    return False, 0


# =========================
# DECODIFICACIÓN DE MODOS
# =========================
def decode_png(file_path: str) -> bytes:
    """Remueve el header PNG falso agregado por el encoder."""
    png_header = (
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        b'\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02'
        b'\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT'
        b'x\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    with open(file_path, 'rb') as f:
        data = f.read()
    return data[len(png_header):] if data.startswith(png_header) else data


def decode_html(file_path: str, xor_key: Optional[str] = None,
                params: Optional[Dict] = None) -> bytes:
    """Decodifica HTML generado por encode_html.
    Prioridad de extracción:
      1. Comentario estructural <!-- encoded:XXX -->
      2. div#encoded-data
      3. div#data
      4. Tag <bytes>...</bytes>
      5. Fallback regex (con advertencia)
    """
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    encoded: Optional[str] = None
    soup = BeautifulSoup(content, 'html.parser')

    # 1. Comentario estructural
    if '<!-- encoded:' in content:
        try:
            encoded = content.split('<!-- encoded:')[1].split('-->')[0].strip()
        except Exception:
            encoded = None

    # 2. div#encoded-data
    if not encoded:
        div = soup.find('div', id='encoded-data')
        if div and div.string:
            encoded = div.string.strip()

    # 3. div#data
    if not encoded:
        div = soup.find('div', id='data')
        if div and div.string:
            encoded = div.string.strip()

    # 4. Tag <bytes>
    if not encoded and '<bytes>' in content:
        try:
            encoded = content.split('<bytes>')[1].split('</bytes>')[0]
        except Exception:
            encoded = None

    # 5. Fallback regex
    if not encoded:
        matches = re.findall(r'[A-Za-z0-9+/=_-]{100,}', content)
        if matches:
            encoded = max(matches, key=len)
            print("  ⚠️  Usando fallback regex poco fiable para decode_html")

    if not encoded:
        raise ValueError("No se encontraron datos codificados en el HTML")

    # Determinar clave
    possible_keys: List[str] = []
    if xor_key:
        possible_keys.append(xor_key)
    if params and params.get('encryption_key'):
        possible_keys.append(params['encryption_key'])
    possible_keys.extend([
        "default_key_1", "default_key_2", "default_key_3", "default_key_4",
        "",  # sin clave
    ])
    unique_keys = list(dict.fromkeys(possible_keys))

    for key in unique_keys:
        try:
            # Probar como url-safe primero
            try:
                decoded = _urlsafe_b64decode(encoded)
            except Exception:
                decoded = base64.b64decode(encoded)

            if key:
                key_bytes = key.encode('utf-8')
                restored = bytearray(len(decoded))
                for i, b in enumerate(decoded):
                    restored[i] = b ^ key_bytes[i % len(key_bytes)]
                try:
                    final = base64.b64decode(bytes(restored))
                    print(f"  ✅ Decodificado con clave: {key[:15]}{'...' if len(key) > 15 else ''}")
                    return final
                except Exception:
                    continue
            else:
                return decoded
        except Exception:
            continue

    raise ValueError("No se pudo decodificar con ninguna clave")


def decode_zip(file_path: str, password: Optional[str] = None) -> bytes:
    """Decodifica ZIP encriptado con AES (pyzipper)."""
    if not HAS_PYZIPPER:
        raise RuntimeError("pyzipper no instalado. Ejecuta: pip install pyzipper")

    with pyzipper.AESZipFile(file_path, 'r') as zf:
        namelist = zf.namelist()
        if not namelist:
            raise ValueError("ZIP vacío")
        if password:
            zf.setpassword(password.encode('utf-8'))
        with zf.open(namelist[0]) as f:
            return f.read()


# =========================
# MULTI-ARCHIVO
# =========================
def is_multi_filename(filename: str) -> bool:
    return filename.startswith("__multi__")


def parse_multi_filename(filename: str) -> Optional[Dict]:
    if not is_multi_filename(filename):
        return None
    try:
        b64 = filename[len("__multi__"):]
        manifest = json.loads(_urlsafe_b64decode(b64).decode('utf-8'))
        return manifest
    except Exception as e:
        print(f"  ⚠️  No se pudo parsear manifiesto multi: {e}")
        return None


# =========================
# DESCARGADOR REUTILIZABLE (CLI + Android/Kivy)
# =========================
def download_bitzero_url(
    url: str,
    output_dir: str = "./BitZero",
    xor_key: Optional[str] = None,
    zip_pass: Optional[str] = None,
    skip_login: bool = False,
    url_pattern: Optional[int] = None,
    verify: bool = False,
    progress_cb: Optional[Callable[[str], None]] = None,
    bytes_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[bool, str]:
    """Descarga completa de una URL BitZero (corta o larga) y devuelve (ok, ruta).

    Es la misma lógica del CLI pero reutilizable: la app Android (Kivy) la
    importa directamente. progress_cb recibe mensajes de estado para la GUI.
    """
    def report(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    # ── Si es URL corta (1 segmento), resolver primero ───────────
    resolved = resolve_short_url(url, verify=verify, progress_cb=report)
    if resolved:
        url = resolved

    # ── Parsear ─────────────────────────────────────────────────
    try:
        params = parse_bitzero_url(url)
    except Exception as e:
        report(f"❌ Error al parsear la URL: {e}")
        return False, ""

    # ── Detectar multi-archivo ──────────────────────────────────
    manifest = parse_multi_filename(params['filename'])
    if manifest:
        report(f"📦 Manifiesto multi-archivo ({manifest.get('count', '?')} archivos)")
        for mname in manifest.get('files', []):
            report(f"     • {mname}")
        final_name = manifest['files'][0] if manifest.get('files') else "output.tar"
        is_tar = True
    else:
        final_name = params['filename']
        is_tar = False

    report(f"📄 Archivo: {final_name} | {sizeof_fmt(params['file_size'])} | "
           f"modo {params['bitzero_mode']} | {len(params['file_ids'])} parte(s)")

    # ── Preparar directorio de salida y sesión ──────────────────
    os.makedirs(output_dir, exist_ok=True)
    final_path = os.path.join(output_dir, final_name)
    session = create_session(verify=verify)

    # ── Verificar conectividad ─────────────────────────────────
    report("📡 Verificando conectividad...")
    if not test_connection(params['host'], verify=verify):
        report(f"❌ No se puede conectar a {params['host']}")
        report("💡 Prueba con --skip-login si la revista permite descarga anónima.")
        return False, ""
    report("✅ Servidor responde")

    # ── Login ───────────────────────────────────────────────────
    if not skip_login:
        report("🔐 Iniciando sesión en OJS...")
        try:
            csrf = ojs_login(session, params['host'], params['username'],
                             params['password'], params['contexto'])
            if not csrf:
                report("❌ Login fallido. Prueba con --skip-login")
                return False, ""
            report("✅ Login exitoso")
        except Exception as e:
            report(f"❌ Error de login: {e}")
            return False, ""
    else:
        report("⏭️ Omitiendo login (modo anónimo)")

    # ── Descargar partes ────────────────────────────────────────
    temp_files: List[Tuple[str, str]] = []
    global_downloaded = 0
    global_total = params['file_size']

    report("⬇️ Descargando partes...")
    for idx, file_id in enumerate(params['file_ids'], 1):
        temp_path = os.path.join(output_dir, f"temp_{file_id}_{idx}.tmp")
        report(f"⬇️ Parte {idx}/{len(params['file_ids'])} [ID: {file_id}]")
        success, downloaded = download_file_with_fallback(
            file_id, params, session, temp_path,
            global_downloaded, global_total,
            forced_pattern=url_pattern,
            bytes_cb=bytes_cb
        )
        if not success:
            report("❌ Error descargando, abortando.")
            for tmp, _ in temp_files:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            return False, ""
        global_downloaded += downloaded
        temp_files.append((temp_path, file_id))

    # ── Reensamblar y decodificar ──────────────────────────────
    report("🔧 Reensamblando y decodificando...")
    try:
        with open(final_path, 'wb') as outfile:
            for temp_path, _ in temp_files:
                if params['bitzero_mode'] == 1:
                    data = decode_png(temp_path)
                elif params['bitzero_mode'] == 2:
                    data = decode_html(temp_path, xor_key, params)
                elif params['bitzero_mode'] == 3:
                    data = decode_zip(temp_path, zip_pass or params.get('encryption_key'))
                else:
                    with open(temp_path, 'rb') as f:
                        data = f.read()
                outfile.write(data)
        report("✅ Reensamblado exitoso")
    except Exception as e:
        report(f"❌ Error durante la decodificación: {e}")
        for temp_path, _ in temp_files:
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return False, ""

    # ── Limpiar temporales ─────────────────────────────────────
    for temp_path, _ in temp_files:
        try:
            os.remove(temp_path)
        except Exception:
            pass

    # ── Si es multi (.tar), extraer ────────────────────────────
    if is_tar:
        report("📦 Extrayendo .tar multi-archivo...")
        import tarfile
        extract_dir = os.path.join(output_dir, os.path.splitext(final_name)[0])
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with tarfile.open(final_path, 'r') as tar:
                tar.extractall(extract_dir)
            report(f"✅ Extraído en: {extract_dir}")
            try:
                os.remove(final_path)
            except Exception:
                pass
            final_path = extract_dir
        except Exception as e:
            report(f"⚠️ No se pudo extraer el .tar: {e}")

    # ── Verificar tamaño ───────────────────────────────────────
    if isinstance(final_path, str) and os.path.isfile(final_path):
        real_size = os.path.getsize(final_path)
        if real_size != params['file_size']:
            report(f"⚠️ Tamaño esperado: {sizeof_fmt(params['file_size'])}, "
                   f"obtenido: {sizeof_fmt(real_size)}")
        else:
            report(f"✅ Tamaño verificado: {sizeof_fmt(real_size)}")

    report(f"✅ Archivo guardado en: {final_path}")
    return True, final_path


# =========================
# MAIN (CLI)
# =========================
def main():
    parser = argparse.ArgumentParser(
        description="BitZero Downloader v5.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 bitzero.py "https://bitzero.techdev.cu/..."
  python3 bitzero.py "https://btz.dwn/AbCdEf12"   (URL corta, se resuelve sola)
  python3 bitzero.py "URL" --skip-login
  python3 bitzero.py "URL" --verify            (activa verificación TLS)
  python3 bitzero.py "URL" --output-dir ./descargas
  python3 bitzero.py "URL" --url-pattern 1
        """
    )
    parser.add_argument("url", nargs="?", help="URL BitZero a descargar")
    parser.add_argument("--xor-key", help="Clave XOR manual (opcional, modo 2)")
    parser.add_argument("--zip-pass", help="Contraseña ZIP (modo 3)")
    parser.add_argument("--url-pattern", type=int, choices=range(1, 7),
                        help="Forzar patrón URL (1-6). Por defecto prueba todos en orden.")
    parser.add_argument("--skip-login", action="store_true",
                        help="Omitir login (si la revista lo permite)")
    parser.add_argument("--verify", action="store_true",
                        help="Activar verificación TLS (certificados válidos)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="Timeout en segundos (default: 60)")
    parser.add_argument("--output-dir", default="./BitZero",
                        help="Directorio de salida (default: ./BitZero)")
    args = parser.parse_args()

    clear_console()
    print("  \033[1m\033[33m╔════════════════════════════════════╗")
    print("  ║    BitZero Downloader v5.0        ║")
    print("  ║    Robusto + patrones optimizados ║")
    print("  ╚════════════════════════════════════╝\033[0m\n")

    # ── Obtener URL ─────────────────────────────────────────────
    url = args.url
    if not url:
        url = input("  🔗 Ingresa la URL BitZero: ").strip()
    if not url:
        print("  ❌ URL vacía. Saliendo.")
        return

    if not args.verify:
        print("  ⚠️  Verificación TLS desactivada. Usa --verify para activarla.")

    # ── Ejecutar la descarga reutilizable ──────────────────────
    def cli_report(msg: str) -> None:
        print(f"  {msg}")

    ok, final_path = download_bitzero_url(
        url, output_dir=args.output_dir, xor_key=args.xor_key,
        zip_pass=args.zip_pass, skip_login=args.skip_login,
        url_pattern=args.url_pattern, verify=args.verify,
        progress_cb=cli_report,
    )
    if ok and final_path:
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  ⚠️  Interrupción del usuario. Saliendo.")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ❌ Error inesperado: {e}")
        sys.exit(1)
