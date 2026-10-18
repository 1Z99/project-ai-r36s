#!/bin/sh
# 227 AI launcher for the R36S (dArkOSRE). Installed by install.sh.
export SDL_VIDEODRIVER=kmsdrm
export SDL_RENDER_DRIVER=software
export LD_LIBRARY_PATH=/roms2/tools/project_ai/runtime

pkill -f '/roms2/tools/project_ai/runtime/llama-server' 2>/dev/null || true
pkill -f '/roms2/tools/project_ai_r36s.py' 2>/dev/null || true
sleep 1
exec /usr/bin/python3 /roms2/tools/project_ai_r36s.py
