[app]

# Nombre visible de la app
title = BitDrop

# Identificador de paquete (invierte un dominio que controlas)
package.name = downloader
package.domain = com.bitdrop

# Directorio con el código (main.py + bitzero.py deben estar aquí)
source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

# Librerías: pyzipper es Python puro; pycryptodomex solo se necesita
# para el modo 3 (ZIP AES). Si el build falla con pycryptodomex, quítala:
# la app seguirá funcionando para modos 0/1/2.
requirements = python3,kivy,requests,beautifulsoup4,pyzipper,pycryptodomex,urllib3,certifi

orientation = portrait
fullscreen = 0

# Permisos Android (la app guarda en su directorio propio vía
# getExternalFilesDir, así que solo necesita INTERNET)
android.permissions = INTERNET

# API / SDK / NDK (recomendado 2026)
android.api = 35
android.minapi = 21
android.ndk = 25b
android.sdk = 35
android.accept_sdk_license = True

# Arquitecturas (arm64 = la mayoría de teléfonos modernos)
android.archs = arm64-v8a, armeabi-v7a

# Mantener en segundo plano no es necesario
android.allow_backup = True

# Fijamos p4a a la última release oficial en PyPI (2026.5.9) en vez de la
# punta de "master". La rama master de python-for-android está en
# desarrollo activo y actualmente su recipe hostpython3 ejecuta
# "pip install -U pip" sin fijar versión (build.py: run_pymodules_install),
# lo que descarga la última versión de pip de PyPI y rompe con
# "ImportError: cannot import name 'open_rich_spinner'" por un desajuste
# interno entre esa versión de pip y el resto del entorno. Un release
# etiquetado es un snapshot estable que no cambia bajo nuestros pies.
p4a.branch = v2026.05.09

[buildozer]

log_level = 2
warn_on_root = 1
