# Guía de despliegue — BitDrop

**BitDrop** es el nombre de la app (descargador BitZero). Cubrimos: URLs
cortas, primer build de la APK, bot de Telegram que publica las compilaciones,
y versiones Windows e iOS.

---

## 1. URLs cortas (tu servidor tiene IP pública)

El acortador corre **dentro del bot** (módulo `bot/shortener.py`, puerto `8080`).

### Opción A — Rápida (IP directa, sin dominio)
```bash
sudo ufw allow 8080/tcp     # o iptables/firewalld equivalente
```
En `bot/.env`:
```
SHORT_URL_BASE=http://TU_IP_PUBLICA:8080
```
Reinicia el bot → URLs tipo `http://TU_IP:8080/AbCdEf12` (funcionan igual).

### Opción B — Bonita (dominio + HTTPS)
1. Dominio o DDNS gratis (duckdns.org) → registro A → tu IP.
2. **Caddy** (`Caddyfile`), HTTPS automático:
   ```
   btz.tudominio.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```
   O **Nginx + certbot**:
   ```nginx
   server {
       server_name btz.tudominio.com;
       location / { proxy_pass http://127.0.0.1:8080; }
   }
   ```
3. `bot/.env`: `SHORT_URL_BASE=https://btz.tudominio.com`

⚠️ El bot debe estar **corriendo** para resolver URLs cortas.

---

## 2. Primer build de la APK (Android)

1. Crea un repositorio en GitHub (vacío) y súbele todo el proyecto:
   ```bash
   cd /ruta/a/frdown
   git init && git add -A && git commit -m "BitDrop"
   git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
   git push -u origin main
   ```
2. En GitHub: pestaña **Actions** → **Build APK Android** → **Run workflow**.
3. Espera ~20–40 min (la primera vez descarga Android SDK/NDK).
4. Cuando termine: entra al job → sección **Artifacts** → descarga
   `bitdrop-apk` → instala el `.apk` en el teléfono (activa "Instalar apps
   desconocidas").

> El build se relanza automáticamente cada vez que modifiques `android/`.

---

## 3. Bot de Telegram que publica cada compilación

Cada build (APK, Windows, iOS) se sube automáticamente a tu canal.

### Configuración (una sola vez)
1. **Crea el bot**: habla con [@BotFather](https://t.me/BotFather) → `/newbot`
   → te da el **token**.
2. **Añade el bot a tu canal** como administrador (con permisos de publicación).
3. **Consigue el chat ID**:
   - Canal público: `@nombrecanal`.
   - Canal privado: envía cualquier mensaje al canal y luego reenvíalo a
     [@userinfobot](https://t.me/userinfobot) → te da el ID (formato
     `-100xxxxxxxxxx`).
4. En GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**:
   - `TELEGRAM_BOT_TOKEN` = el token de BotFather
   - `TELEGRAM_CHAT_ID` = el ID del canal

Listo: cada vez que se compile, la app aparecerá en el canal. Si no
configuras los secretos, el build **no falla** — solo se omite la publicación.

### Uso local (opcional)
```bash
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx python3 tools/publish_to_telegram.py ruta/al.apk "caption"
# o
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=-100xxx bash tools/tg_publish.sh ruta/al.apk "caption"
```

---

## 4. Versión Windows

App con interfaz (Tkinter) en `windows/bitdrop_win.py`. Se compila sola en
GitHub Actions (workflow **Build Windows**) y publica el `.exe` en tu canal.

- **Compilar en la nube**: Actions → **Build Windows** → Run workflow → el
  artefacto `bitdrop-windows` contiene `BitDrop.exe` (portable, no necesita
  Python instalado).
- **Compilar en tu PC Windows**:
  ```bat
  pip install pyinstaller requests beautifulsoup4 urllib3 pyzipper
  pyinstaller --onefile --windowed --name BitDrop windows\bitdrop_win.py
  ```
  Resultado en `dist\BitDrop.exe`.
- Los archivos se guardan en `%USERPROFILE%\Downloads\BitDrop\`.

---

## 5. Versión iOS

⚠️ **Realidad honesta**: iOS solo se puede compilar en **macOS** y, para
instalar en un iPhone real, necesitas **Apple Developer Program (99 USD/año)**
+ certificados + provisioning profile. Sin eso, solo obtienes un build de
**simulador** (no instalable en dispositivo).

El workflow **Build iOS** (`build-ios.yml`) está preparado:
- Corre en un runner macOS (gratis dentro de la cuota de Actions).
- Compila con kivy-ios (primer build lento: 30–60 min).
- Por defecto produce un build de **simulador sin firmar** (prueba que
  compila). El paso de firma para dispositivo está comentado dentro del
  workflow: descoméntalo cuando tengas la cuenta de desarrollador.

**Alternativa práctica**: si quieres una "versión iOS" sin pagar, la opción
realista es que los usuarios usen la **APK** (Android) o el **.exe** (Windows).

---

## Recordatorios

- **Sincronizar el downloader**: `cp downloader/bitzero.py android/bitzero.py`
  y `cp downloader/bitzero.py windows/bitzero.py` (los CI también lo hacen).
- **Estructura**:
  ```
  bot/shortener.py                  # acortador de URLs (aiohttp)
  android/                          # app Kivy + buildozer.spec
  windows/bitdrop_win.py            # app Tkinter
  tools/publish_to_telegram.py      # helper publicación Telegram
  tools/tg_publish.sh               # helper shell para CI
  .github/workflows/build-apk.yml   # build Android + publicación
  .github/workflows/build-windows.yml
  .github/workflows/build-ios.yml
  ```
