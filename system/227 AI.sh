#!/bin/sh
# 227 AI launcher for the R36S (dArkOSRE). Installed by install227ai.sh.
# It finds its own folder, so it works whether games live on the main SD
# card (/roms) or on the second one (/roms2).
export SDL_VIDEODRIVER=kmsdrm
export SDL_RENDER_DRIVER=software
if [ -f /usr/lib/aarch64-linux-gnu/libMali.so ]; then
    export SDL_VIDEO_EGL_DRIVER="${SDL_VIDEO_EGL_DRIVER:-/usr/lib/aarch64-linux-gnu/libMali.so}"
    export SDL_VIDEO_GL_DRIVER="${SDL_VIDEO_GL_DRIVER:-/usr/lib/aarch64-linux-gnu/libMali.so}"
fi

SYSDIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
TOOLS="$(dirname "$SYSDIR")/tools"
export LD_LIBRARY_PATH="$TOOLS/project_ai/runtime"

if [ ! -f "$TOOLS/project_ai_r36s.py" ]; then
    printf '\n'
    printf '  227 AI is not installed at:\n'
    printf '    %s\n\n' "$TOOLS"
    printf '  Open Tools and run "Install 227 AI".\n\n'
    printf '  Press ENTER to return to EmulationStation...'
    read -r dummy 2>/dev/null || true
    printf '\n'
    exit 1
fi

pkill -f "$TOOLS/project_ai/runtime/llama-server" 2>/dev/null || true
pkill -f "$TOOLS/project_ai_r36s.py" 2>/dev/null || true
sleep 1
exec /usr/bin/python3 "$TOOLS/project_ai_r36s.py"
