# Claude-movile · Control remoto de tu Android desde el navegador

Ver la pantalla en vivo, tocar, deslizar, botones del sistema, escribir, abrir apps/URLs
(YouTube, etc.), capturar imagen y grabar vídeo. Usa ADB; solo biblioteca estándar de Python.

## Requisitos
- Python 3.8+, [`adb`](https://developer.android.com/tools/releases/platform-tools) en el PATH.
- (Opcional) `pip install pillow` → fotogramas JPEG reducidos, mucho más fluido.
- En el móvil: Ajustes → Acerca del teléfono → pulsa 7 veces "Número de compilación" →
  Opciones de desarrollador → **Depuración USB** (y, para tocar, "Depuración USB (ajustes de seguridad)" en Xiaomi).

## Uso
```bash
adb devices                  # acepta el aviso de autorización en el móvil
python3 server.py            # abre la URL que imprime (incluye token)
```
Por Wi-Fi (Android 11+): Opciones de desarrollador → Depuración inalámbrica →
`python3 server.py --connect IP:PUERTO` (tras `adb pair IP:PUERTO_EMPAREJAMIENTO`).

Opciones: `--serial`, `--fps 8`, `--scale 0.6`, `--quality 60`, `--port 8080`, `--host 0.0.0.0`.

Capturas y grabaciones se guardan en `captures/`.

## Vídeo fluido y audio
El navegador muestra ~5-10 fps (captura de pantalla por ADB): válido para navegar, no para ver
vídeo. Para vídeo a 60 fps con audio (Android 11+) usa `./scrcpy.sh` (requiere scrcpy).

## Seguridad
- Escucha solo en `127.0.0.1` y exige un token aleatorio por ejecución. Si usas `--host 0.0.0.0`
  hazlo solo en redes de confianza (no hay HTTPS): quien tenga el token controla tu móvil.
- Algunas apps (banca, DRM) bloquean la captura de pantalla (FLAG_SECURE) y se verán en negro.
- `input text` solo admite ASCII (sin tildes/emoji).
- Úsalo únicamente en dispositivos tuyos.

## Nota sobre Claude
Este código corre en tu equipo, que es el que tiene acceso a tu móvil. Para que Claude lo maneje
directamente, ejecuta Claude Code en ese equipo y pídele que use `adb`/esta API
(`/api/tap`, `/api/key`, `/api/screenshot`…).
