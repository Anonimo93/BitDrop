"""
BitDrop — Descargador BitZero para Android (Kivy).

Reutiliza la lógica de descarga de bitzero.py (incluido en este mismo
directorio) a través de download_bitzero_url(), que ya soporta URLs
cortas (https://btz.dwn/AbCdEf12) y largas.

Los archivos se guardan en /storage/emulated/0/BitZero/ (visible en el
gestor de archivos).
"""
from __future__ import annotations

import os
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

import bitzero  # la lógica del downloader

# Carpeta de descargas.
# En Android usamos getExternalFilesDir(None) → /storage/emulated/0/Android/
# data/<paquete>/files/BitZero: escribible en TODOS los Android (scoped storage
# bloquea la raíz compartida en API 29+). En escritorio: ~/BitZero.
try:
    from jnius import autoclass
    _PythonActivity = autoclass('org.kivy.android.PythonActivity')
    _files_dir = _PythonActivity.mActivity.getExternalFilesDir(None).getAbsolutePath()
    DOWNLOAD_DIR = os.path.join(_files_dir, "BitZero")
except Exception:
    DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "BitZero")


class BitZeroUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=14, spacing=10, **kwargs)
        self._last_progress = 0.0

        title = Label(
            text="[b]BitDrop[/b] — Descargador BitZero",
            markup=True, size_hint_y=None, height=36, font_size=20)
        self.add_widget(title)

        self.url_input = TextInput(
            hint_text="Pega aquí la URL BitZero (corta o larga)...",
            multiline=False, size_hint_y=None, height=48)
        self.add_widget(self.url_input)

        self.btn = Button(text="⬇️  Descargar", size_hint_y=None, height=54,
                          background_color=(0.16, 0.55, 0.9, 1))
        self.btn.bind(on_press=self.on_download)
        self.add_widget(self.btn)

        self.status = Label(
            text="Listo. Pega una URL y pulsa Descargar.",
            size_hint_y=None, height=36, halign="center", valign="middle")
        self.add_widget(self.status)

        # Barra de progreso de descarga (bytes)
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=18)
        self.add_widget(self.progress)
        self.pct_label = Label(
            text="0%", size_hint_y=None, height=22, halign="center")
        self.add_widget(self.pct_label)

        # Log de pasos (scrollable)
        self.log = Label(
            text="", halign="left", valign="top",
            font_size=13, size_hint_y=None)
        self.log.bind(texture_size=self._on_log_resize)
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self.log)
        self.add_widget(scroll)

    def _on_log_resize(self, _label, _size):
        # Envuelve el texto al ancho y crece el alto para que el scroll funcione
        self.log.text_size = (self.log.width, None)
        self.log.height = max(self.log.texture_size[1], 40)

    def _set(self, widget, text):
        def upd(_dt):
            widget.text = text
        Clock.schedule_once(upd)

    def _append_log(self, msg):
        def upd(_dt):
            self.log.text = (msg + "\n" + self.log.text)[:4000]
        Clock.schedule_once(upd)

    def _update_progress(self, done, total):
        # Throttle: como mucho una actualización cada 0.2s (evita saturar
        # la UI con miles de schedule_once en archivos grandes)
        now = time.time()
        if done < total and now - self._last_progress < 0.2:
            return
        self._last_progress = now

        def upd(_dt):
            pct = (done / total * 100) if total > 0 else 0
            self.progress.value = min(pct, 100)
            self.pct_label.text = f"{pct:.1f}%  ({bitzero.sizeof_fmt(done)} / {bitzero.sizeof_fmt(total)})"
        Clock.schedule_once(upd)

    def on_download(self, _btn):
        url = self.url_input.text.strip()
        if not url:
            self._set(self.status, "❌ Pega una URL primero")
            return
        self.btn.disabled = True
        self._set(self.status, "⏳ Descargando...")
        threading.Thread(target=self._worker, args=(url,), daemon=True).start()

    def _worker(self, url):
        def report(msg: str):
            self._set(self.status, msg)
            self._append_log(msg)

        try:
            ok, path = bitzero.download_bitzero_url(
                url, output_dir=DOWNLOAD_DIR, verify=False,
                progress_cb=report, bytes_cb=self._update_progress)
            if ok:
                self._set(self.status, f"✅ Listo: {path}")
                self._set(self.pct_label, "100%")
            else:
                self._set(self.status, "❌ Descarga fallida (revisa el log)")
        except Exception as e:
            self._set(self.status, f"❌ Error: {e}")
            self._append_log(f"❌ {e}")
        finally:
            Clock.schedule_once(lambda _dt: setattr(self.btn, "disabled", False))


class BitZeroApp(App):
    def build(self):
        self.title = "BitDrop"
        return BitZeroUI()


if __name__ == "__main__":
    BitZeroApp().run()
