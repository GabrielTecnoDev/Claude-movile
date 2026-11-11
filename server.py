#!/usr/bin/env python3
"""Control remoto de TU móvil Android desde el navegador, vía ADB.

Ver pantalla en vivo, tocar/deslizar, botones del sistema, escribir texto,
abrir apps, capturar imagen y grabar vídeo. Solo biblioteca estándar
(Pillow es opcional: comprime los fotogramas a JPEG y los reduce de tamaño).
"""
import argparse, glob, json, os, secrets, struct, subprocess, shlex, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from PIL import Image
    import io
except ImportError:
    Image = None

ROOT = os.path.dirname(os.path.abspath(__file__))
CAPTURES = os.path.join(ROOT, "captures")
os.makedirs(CAPTURES, exist_ok=True)

KEYS = {  # nombre -> keyevent de Android
    "home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "recents": "KEYCODE_APP_SWITCH",
    "power": "KEYCODE_POWER", "volup": "KEYCODE_VOLUME_UP", "voldown": "KEYCODE_VOLUME_DOWN",
    "enter": "KEYCODE_ENTER", "del": "KEYCODE_DEL", "wake": "KEYCODE_WAKEUP",
    "playpause": "KEYCODE_MEDIA_PLAY_PAUSE", "next": "KEYCODE_MEDIA_NEXT",
    "prev": "KEYCODE_MEDIA_PREVIOUS", "menu": "KEYCODE_MENU", "tab": "KEYCODE_TAB",
    "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT", "right": "KEYCODE_DPAD_RIGHT",
}


class Device:
    def __init__(self, serial=None, fps=8, scale=0.6, quality=60):
        self.serial, self.fps, self.scale, self.quality = serial, fps, scale, quality
        self.frame, self.frame_id, self.frame_type = None, 0, "image/png"
        self.cond = threading.Condition()
        self.rec = None
        self.rec_name = None

    def adb(self, *args, binary=False, timeout=20):
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace").strip() or "adb falló")
        return r.stdout if binary else r.stdout.decode(errors="replace")

    def sh(self, cmd, **kw):
        return self.adb("shell", cmd, **kw)

    # --- captura -----------------------------------------------------------
    def screencap(self):
        data = self.adb("exec-out", "screencap", "-p", binary=True)
        if not data.startswith(b"\x89PNG"):
            raise RuntimeError("screencap devolvió datos no válidos (¿pantalla bloqueada con FLAG_SECURE?)")
        return data

    def encode(self, png):
        if Image is None:
            return png, "image/png"
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if self.scale < 1:
            im = im.resize((int(im.width * self.scale), int(im.height * self.scale)))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=self.quality)
        return out.getvalue(), "image/jpeg"

    def capture_loop(self):
        delay = 1.0 / self.fps
        while True:
            t0 = time.time()
            try:
                f, ct = self.encode(self.screencap())
                with self.cond:
                    self.frame, self.frame_type = f, ct
                    self.frame_id += 1
                    self.cond.notify_all()
            except Exception as e:
                print("[captura]", e)
                time.sleep(2)
            time.sleep(max(0, delay - (time.time() - t0)))

    # --- entrada -----------------------------------------------------------
    def tap(self, x, y): self.sh(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.sh(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def key(self, name):
        code = KEYS.get(name) or (name if name.startswith("KEYCODE_") else None)
        if not code: raise ValueError("tecla desconocida: " + name)
        self.sh(f"input keyevent {code}")

    def text(self, s):
        # `input text` solo soporta ASCII; espacios -> %s
        esc = "".join("%s" if c == " " else ("\\" + c if c in "\\'\"&|;<>()$`!*?#~{}[]^" else c) for c in s)
        self.sh("input text " + shlex.quote(esc))

    # --- apps --------------------------------------------------------------
    def apps(self):
        out = self.sh("pm list packages -3")
        return sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l)

    def launch(self, pkg):
        self.sh(f"monkey -p {shlex.quote(pkg)} -c android.intent.category.LAUNCHER 1")

    def open_url(self, url):
        self.sh("am start -a android.intent.action.VIEW -d " + shlex.quote(url))

    def info(self):
        return {
            "model": self.sh("getprop ro.product.model").strip(),
            "android": self.sh("getprop ro.build.version.release").strip(),
            "size": self.sh("wm size").strip().replace("Physical size: ", ""),
            "battery": self.sh("dumpsys battery | grep level").strip().replace("level: ", ""),
        }

    # --- archivos ----------------------------------------------------------
    def save_screenshot(self):
        name = time.strftime("screenshot_%Y%m%d_%H%M%S.png")
        with open(os.path.join(CAPTURES, name), "wb") as f:
            f.write(self.screencap())
        return name

    def rec_start(self):
        if self.rec: raise RuntimeError("ya se está grabando")
        self.rec_name = time.strftime("rec_%Y%m%d_%H%M%S.mp4")
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + [
            "shell", "screenrecord", "--time-limit", "180", "/sdcard/_rec.mp4"]
        self.rec = subprocess.Popen(cmd)

    def rec_stop(self):
        if not self.rec: raise RuntimeError("no hay grabación")
        try: self.sh("pkill -2 screenrecord")  # SIGINT para cerrar el mp4 bien
        except RuntimeError: pass
        self.rec.wait(timeout=15); self.rec = None
        time.sleep(1)
        self.adb("pull", "/sdcard/_rec.mp4", os.path.join(CAPTURES, self.rec_name), timeout=120)
        self.sh("rm /sdcard/_rec.mp4")
        return self.rec_name


class Handler(BaseHTTPRequestHandler):
    dev: Device = None
    token = ""

    def log_message(self, *a): pass

    def _auth(self, q):
        cookie = self.headers.get("Cookie", "")
        return secrets.compare_digest(
            (q.get("t", [""])[0] or self.headers.get("X-Token", "")
             or next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("t=")), "")),
            self.token)

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if not self._auth(q): return self._send(401, {"error": "token inválido"})
        d = self.dev
        try:
            if u.path == "/":
                with open(os.path.join(ROOT, "static", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8",
                                      {"Set-Cookie": f"t={self.token}; Path=/; SameSite=Strict"})
            if u.path == "/stream": return self._stream()
            if u.path == "/api/info": return self._send(200, d.info())
            if u.path == "/api/apps": return self._send(200, d.apps())
            if u.path == "/api/screenshot":
                name = d.save_screenshot()
                with open(os.path.join(CAPTURES, name), "rb") as f:
                    return self._send(200, f.read(), "image/png",
                                      {"Content-Disposition": f'attachment; filename="{name}"'})
            if u.path == "/api/files":
                return self._send(200, sorted(os.path.basename(p) for p in glob.glob(CAPTURES + "/*")))
            if u.path.startswith("/captures/"):
                p = os.path.join(CAPTURES, os.path.basename(u.path))
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        return self._send(200, f.read(), "video/mp4" if p.endswith(".mp4") else "image/png")
            self._send(404, {"error": "no encontrado"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if not self._auth(q): return self._send(401, {"error": "token inválido"})
        n = int(self.headers.get("Content-Length") or 0)
        b = json.loads(self.rfile.read(n) or b"{}"); d = self.dev
        try:
            p = u.path
            if p == "/api/tap": d.tap(b["x"], b["y"])
            elif p == "/api/swipe": d.swipe(b["x1"], b["y1"], b["x2"], b["y2"], b.get("ms", 300))
            elif p == "/api/key": d.key(b["key"])
            elif p == "/api/text": d.text(b["text"])
            elif p == "/api/launch": d.launch(b["package"])
            elif p == "/api/url": d.open_url(b["url"])
            elif p == "/api/rec/start": d.rec_start()
            elif p == "/api/rec/stop": return self._send(200, {"file": d.rec_stop()})
            else: return self._send(404, {"error": "no encontrado"})
            self._send(200, {"ok": True})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _stream(self):
        d = self.dev
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        last = 0
        try:
            while True:
                with d.cond:
                    d.cond.wait_for(lambda: d.frame_id != last, timeout=10)
                    f, ct, last = d.frame, d.frame_type, d.frame_id
                if f is None: continue
                self.wfile.write(b"--frame\r\nContent-Type: %s\r\nContent-Length: %d\r\n\r\n" % (ct.encode(), len(f)))
                self.wfile.write(f); self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--serial", help="dispositivo ADB (adb devices)")
    ap.add_argument("--connect", help="IP:puerto para adb connect (Wi-Fi)")
    ap.add_argument("--host", default="127.0.0.1", help="usa 0.0.0.0 para acceder desde otros equipos")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--fps", type=float, default=8)
    ap.add_argument("--scale", type=float, default=0.6, help="reducción (requiere Pillow)")
    ap.add_argument("--quality", type=int, default=60, help="calidad JPEG (requiere Pillow)")
    a = ap.parse_args()

    if a.connect: subprocess.run(["adb", "connect", a.connect], check=False)
    dev = Device(a.serial, a.fps, a.scale, a.quality)
    print("Dispositivo:", dev.info())
    if Image is None: print("Aviso: sin Pillow se envía PNG completo (más lento). pip install pillow")
    Handler.dev, Handler.token = dev, secrets.token_urlsafe(16)
    threading.Thread(target=dev.capture_loop, daemon=True).start()
    host = "localhost" if a.host == "127.0.0.1" else a.host
    print(f"\nAbre: http://{host}:{a.port}/?t={Handler.token}\n")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
