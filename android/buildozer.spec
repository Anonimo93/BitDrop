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
#
# hostpython3==3.11.9,python3==3.11.9: fijamos la versión de Python que
# python-for-android compila internamente (hostpython) y empaqueta en el
# APK. Sin esto, p4a master compila la última versión de Python disponible
# (actualmente 3.14), cuyo ensurepip genera un pip incompatible con el
# propio código de p4a (ImportError: BuildDependencyInstallError). 3.11
# es una versión ampliamente probada y estable con el toolchain actual.
requirements = hostpython3==3.11.9,python3==3.11.9,kivy,requests,beautifulsoup4,pyzipper,pycryptodomex,urllib3,certifi

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

p4a.branch = master

[buildozer]

log_level = 2
warn_on_root = 1
