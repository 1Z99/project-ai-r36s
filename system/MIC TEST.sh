#!/bin/sh
# Microphone / capture-card tester for the R36S. Installed by install.sh.
export SDL_VIDEODRIVER=kmsdrm
export SDL_RENDER_DRIVER=software

pkill -f '/roms2/tools/project_ai_r36s.py' 2>/dev/null || true
sleep 1
exec /usr/bin/python3 /roms2/tools/mic_test.py
