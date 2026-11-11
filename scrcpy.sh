#!/usr/bin/env bash
# Alternativa fluida (30-60 fps, audio, ratón/teclado reales) con scrcpy: https://github.com/Genymobile/scrcpy
# Uso: ./scrcpy.sh [IP:puerto]   (sin argumento usa el móvil conectado por USB)
set -e
[ -n "$1" ] && adb connect "$1"
exec scrcpy --max-fps 60 --stay-awake --turn-screen-off --show-touches --record "captures/scrcpy_$(date +%Y%m%d_%H%M%S).mp4" ${1:+--serial "$1"}
