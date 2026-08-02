"""
BitDrop — Descargador BitZero para Windows (Tkinter, sin dependencias extra).

Compila con PyInstaller (ver .github/workflows/build-windows.yml):
    pyinstaller --onefile --windowed --name BitDrop windows/bitdrop_win.py

Los archivos se guardan en %USERPROFILE%/Downloads/BitDrop/
"""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import ttk

import bitzero  # la lógica del downloader (se sincroniza en el CI)

DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "BitDrop")


class BitDropApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("BitDrop — Descargador BitZero")
        root.geometry("540x480")
        self._last_progress = 0.0

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="URL BitZero (corta o larga):").pack(anchor="w")
        self.url_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.url_var).pack(fill="x", pady=(2, 8))

        self.btn = ttk.Button(frm, text="⬇️  Descargar", command=self.on_download)
        self.btn.pack(fill="x")

        self.status_var = tk.StringVar(value="Listo. Pega una URL y pulsa Descargar.")
        ttk.Label(frm, textvariable=self.status_var).pack(anchor="w", pady=(8, 2))

        self.progress = ttk.Progressbar(frm, maximum=100, value=0)
        self.progress.pack(fill="x", pady=4)
        self.pct_var = tk.StringVar(value="0%")
        ttk.Label(frm, textvariable=self.pct_var).pack(anchor="w")

        ttk.Label(frm, text="Log:").pack(anchor="w", pady=(8, 2))
        self.log = tk.Text(frm, height=12, state="disabled")
        self.log.pack(fill="both", expand=True)

    # ── Helpers de UI (seguros desde cualquier hilo) ─────────────────
    def _log(self, msg: str):
        def upd():
            self.log.config(state="normal")
            self.log.insert("1.0", msg + "\n")
            # Recortar a 200 líneas (comparar como entero, no como cadena)
            if int(self.log.index("end-1c").split(".")[0]) > 200:
                self.log.delete("201.0", "end")
            self.log.config(state="disabled")
        self.root.after(0, upd)

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self.status_var.set(msg))

    def _set_progress(self, pct: float, text: str):
        self.root.after(0, lambda: self.progress.config(value=min(pct, 100)))
        self.root.after(0, lambda: self.pct_var.set(text))

    # ── Descarga en hilo ─────────────────────────────────────────────
    def on_download(self):
        url = self.url_var.get().strip()
        if not url:
            self._set_status("❌ Pega una URL primero")
            return
        self.btn.config(state="disabled")
        self.progress.config(value=0)
        self.pct_var.set("0%")
        self._set_status("⏳ Descargando...")
        threading.Thread(target=self._worker, args=(url,), daemon=True).start()

    def _worker(self, url: str):
        def report(msg: str):
            self._set_status(msg)
            self._log(msg)

        def bytes_cb(done: int, total: int):
            # Throttle: como mucho una actualización cada 0.2s
            now = time.time()
            if done < total and now - self._last_progress < 0.2:
                return
            self._last_progress = now
            pct = (done / total * 100) if total > 0 else 0
            self._set_progress(
                pct, f"{pct:.1f}%  ({bitzero.sizeof_fmt(done)} / {bitzero.sizeof_fmt(total)})")

        try:
            ok, path = bitzero.download_bitzero_url(
                url, output_dir=DOWNLOAD_DIR, verify=False,
                progress_cb=report, bytes_cb=bytes_cb)
            if ok:
                self._set_status(f"✅ Listo: {path}")
                self._set_progress(100, "100%")
            else:
                self._set_status("❌ Descarga fallida (revisa el log)")
        except Exception as e:
            self._set_status(f"❌ Error: {e}")
            self._log(f"❌ {e}")
        finally:
            self.root.after(0, lambda: self.btn.config(state="normal"))


def main():
    root = tk.Tk()
    BitDropApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
