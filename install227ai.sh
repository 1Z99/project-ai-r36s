#!/bin/sh
# Bootstrap only. App ownership and card selection belong to the Python manager.
set -eu

help() {
    printf '%s\n' \
        '227 AI graphical install / update / uninstall manager' \
        'Usage: sh install227ai.sh [--source PATH] [--desktop]' \
        '       sh install227ai.sh --cli --roms PATH --action install|update|uninstall [--yes] [--dry-run]' \
        '' \
        'The graphical manager always asks you to select the destination card.' \
        'CLI mutations require --yes; uninstall deletes settings and chats on that card.' \
        'CLI only: --es-cfg PATH, --themes PATH. Dry-run requires an explicit --roms.' \
        'Dry-run uses an existing core only; it never downloads or writes files.' \
        'Development: PROJECT_AI_RAW_BASE, PROJECT_AI_REL_BASE, PROJECT_AI_RUNTIME_LOCAL.'
}

CLI=0 DRY=0 YES=0 DESKTOP=0 SOURCE_SET=0 CLI_FLAGS=0
ROMS='' ACTION='' SOURCE='' THEMES=''
ES_CFG=/etc/emulationstation/es_systems.cfg
LOG='' TEMP=''
fail() {
    printf '227 AI: %s\n' "$*" >&2
    if [ -n "$LOG" ]; then
        printf '227 AI: %s\n' "$*" >> "$LOG" 2>/dev/null || true
    fi
    if [ "$CLI" -eq 0 ] && command -v dialog >/dev/null 2>&1; then
        dialog --title '227 AI bootstrap failed' --msgbox "$*

Log: ${LOG:-unavailable (manager storage not ready)}
Fix the problem and launch Install 227 AI again.
If no terminal is available, inspect the log over SSH." 16 68 || true
    fi
    exit 1
}

parse() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -h|--help) help; exit 0 ;;
            --cli) CLI=1; shift ;;
            --dry-run) DRY=1; CLI_FLAGS=1; shift ;;
            --yes) YES=1; CLI_FLAGS=1; shift ;;
            --desktop) DESKTOP=1; shift ;;
            --roms|--action|--source|--es-cfg|--themes)
                [ "$#" -ge 2 ] && [ -n "$2" ] || fail "Missing value for $1"
                case "$2" in --*) fail "Missing value for $1" ;; esac
                case "$1" in
                    --roms) ROMS=$2; CLI_FLAGS=1 ;;
                    --action) ACTION=$2; CLI_FLAGS=1 ;;
                    --source) SOURCE=$2; SOURCE_SET=1 ;;
                    --es-cfg) ES_CFG=$2; CLI_FLAGS=1 ;;
                    --themes) THEMES=$2; CLI_FLAGS=1 ;;
                esac
                shift 2 ;;
            *) fail "Unknown option: $1 (see --help)" ;;
        esac
    done
}
parse "$@"
[ "$CLI_FLAGS" -eq 0 ] || [ "$CLI" -eq 1 ] || fail 'Use --cli for --roms, --action, --yes, --dry-run, --es-cfg or --themes.'
if [ "$CLI" -eq 1 ]; then
    [ "$DESKTOP" -eq 0 ] || fail '--desktop is graphical only.'
    [ -n "$ROMS" ] || fail '--cli requires an explicit --roms PATH.'
    case "$ACTION" in install|update|uninstall) ;; *) fail '--cli requires --action install|update|uninstall.' ;; esac
    [ "$DRY" -eq 1 ] || [ "$YES" -eq 1 ] || fail 'CLI changes require --yes. Uninstall deletes settings and chats on the selected card.'
fi
command -v python3 >/dev/null 2>&1 || fail 'Python 3 is required.'
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
SELF="$SELF_DIR/$(basename -- "$0")"
MODULES='' LOCAL_REPO=0
if [ -f "$SELF_DIR/installer_core.py" ]; then
    MODULES=$SELF_DIR
    case "$SELF_DIR" in
        /roms/tools/227-ai-manager|/roms2/tools/227-ai-manager) ;;
        *) LOCAL_REPO=1 ;; # A local, self-contained manager/bundle test build.
    esac
elif [ -f "$SELF_DIR/installer/installer_core.py" ]; then
    MODULES="$SELF_DIR/installer"
    LOCAL_REPO=1
    if [ "$SOURCE_SET" -eq 0 ] && [ -d "$SELF_DIR/app" ]; then
        SOURCE=$SELF_DIR
        SOURCE_SET=1
        set -- "$@" --source "$SOURCE"
    fi
fi

# Keep even core-only dry runs usable without SDL, the CLI module, or bootstrap writes.
if [ "$DRY" -eq 1 ]; then
    [ -n "$MODULES" ] || fail 'Dry-run needs an existing installer_core.py beside install227ai.sh or in installer/. Nothing was downloaded or written.'
    exec python3 -B - "$MODULES" "$ROMS" "$ACTION" "$ES_CFG" "$THEMES" <<'PY'
import json
import sys
sys.path.insert(0, sys.argv[1])
from installer_core import Installer, discover_cards
try:
    print(json.dumps(discover_cards(), indent=2))
    worker = Installer(sys.argv[2], es_cfg=sys.argv[4], themes=sys.argv[5] or None)
    worker.validate_card()
    print("DRY RUN: {} on {}. No files written or downloaded.".format(sys.argv[3], worker.prefix))
    print("App/data: {} | System: {} | Manager retained".format(worker.root, worker.sysdir))
    if sys.argv[3] == "uninstall":
        print("WARNING: uninstall would delete settings, chats, history and models on this card.")
except Exception as error:
    print("Dry-run failed: {}".format(error), file=sys.stderr)
    sys.exit(1)
PY
fi

if [ "$LOCAL_REPO" -eq 1 ]; then
    LOG="$MODULES/bootstrap.log"
else
    if [ -z "$MODULES" ]; then
        # Prefer the launcher's own Tools card, not the app's eventual target.
        CARD=$(python3 -B - "$SELF_DIR" <<'PY'
import os
from pathlib import Path
import sys
mounts = {}
with open('/proc/mounts') as stream:
    for line in stream:
        fields = line.split()
        if len(fields) >= 4:
            mounts[fields[1]] = fields[3].split(',')
own = next((card for card in ('/roms', '/roms2')
            if sys.argv[1] == card + '/tools' or sys.argv[1].startswith(card + '/tools/')), None)
for card in dict.fromkeys((own, '/roms2', '/roms')):
    if card is None:
        continue
    path = Path(card)
    tools = path / 'tools'
    if (card in mounts and 'ro' not in mounts[card] and not path.is_symlink()
            and path.is_dir() and os.access(card, os.W_OK | os.X_OK)
            and not tools.is_symlink()
            and (not tools.exists() or tools.is_dir() and os.access(tools, os.W_OK | os.X_OK))):
        print(card)
        sys.exit(0)
sys.exit('No writable SD card is actually mounted at /roms or /roms2.')
PY
        ) || fail 'No writable mounted Tools card. Insert/remount an SD card at /roms or /roms2.'
        MODULES="$CARD/tools/227-ai-manager"
    fi
    # Refuse redirected manager storage, including existing deployed managers.
    python3 -B - "$MODULES" <<'PY' || fail 'Manager storage is not a writable mounted Tools card.'
import os
from pathlib import Path
import sys
path = Path(sys.argv[1])
if path not in (Path('/roms/tools/227-ai-manager'), Path('/roms2/tools/227-ai-manager')):
    sys.exit('Manager must live at <card>/tools/227-ai-manager')
for part in (path,) + tuple(path.parents):
    if part.is_symlink():
        sys.exit('Refusing symlink manager storage: ' + str(part))
card = path.parents[1]
with open('/proc/mounts') as stream:
    mounts = {fields[1]: fields[3].split(',') for fields in map(str.split, stream) if len(fields) >= 4}
if (str(card) not in mounts or 'ro' in mounts[str(card)]
        or not os.access(card, os.W_OK | os.X_OK)):
    sys.exit('Manager card is not mounted and writable')
for name in ('bootstrap.log', 'install.sh', 'installer_ui.py', 'installer_core.py', 'installer_cli.py'):
    if (path / name).is_symlink():
        sys.exit('Refusing symlink manager file: ' + name)
PY
    mkdir -p "$MODULES" || fail 'Cannot create manager directory.'
    LOG="$MODULES/bootstrap.log"
fi
: >> "$LOG" || fail "Cannot write $LOG"
trap '[ -z "$TEMP" ] || rm -rf -- "$TEMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$LOCAL_REPO" -eq 0 ]; then
    TEMP=$(mktemp -d "$MODULES/.bootstrap.XXXXXX") || fail 'Cannot create bootstrap staging directory.'
    if [ ! -f "$MODULES/installer_ui.py" ] || [ ! -f "$MODULES/installer_core.py" ] || [ ! -f "$MODULES/installer_cli.py" ]; then
        command -v curl >/dev/null 2>&1 || fail 'curl is required to download the manager.'
        RAW=${PROJECT_AI_RAW_BASE:-https://raw.githubusercontent.com/1Z99/project-ai-r36s/main}
        for name in installer_core.py installer_ui.py installer_cli.py; do
            printf 'Fetching %s\n' "$name" >> "$LOG"
            curl --fail --location --silent --show-error --connect-timeout 15 \
                --speed-time 30 --speed-limit 1024 --max-time 120 \
                --retry 2 --retry-delay 2 --output "$TEMP/$name" \
                "${RAW%/}/installer/$name" >> "$LOG" 2>&1 || fail "Cannot download $name. Check Wi-Fi and retry. Log: $LOG"
        done
        python3 -B - "$TEMP" <<'PY' >> "$LOG" 2>&1 || fail "Downloaded manager is invalid. Log: $LOG"
from pathlib import Path
import sys
for path in Path(sys.argv[1]).glob('*.py'):
    data = path.read_bytes()
    if not data.strip():
        raise ValueError('Empty manager module: ' + path.name)
    compile(data, str(path), 'exec')
PY
        for name in installer_core.py installer_ui.py installer_cli.py; do
            mv -f -- "$TEMP/$name" "$MODULES/$name" || fail "Cannot publish $name."
        done
    fi
    if [ "$SELF" != "$MODULES/install.sh" ]; then
        cp -- "$SELF" "$TEMP/install.sh" && chmod 755 "$TEMP/install.sh" && \
            mv -f -- "$TEMP/install.sh" "$MODULES/install.sh" || fail 'Cannot retain bootstrap.'
    fi
    ENTRY="$(dirname -- "$MODULES")/Install 227 AI.sh"
    [ ! -L "$ENTRY" ] || fail 'Refusing symlink Tools entry.'
    printf '%s\n' '#!/bin/sh' \
        'DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)' \
        'exec sh "$DIR/227-ai-manager/install.sh" "$@"' > "$TEMP/entry.sh"
    chmod 755 "$TEMP/entry.sh" && mv -f -- "$TEMP/entry.sh" "$ENTRY" || fail 'Cannot publish Tools entry.'
fi

if [ "$CLI" -eq 1 ]; then
    python3 -B "$MODULES/installer_cli.py" "$@"
else
    if [ "$DESKTOP" -eq 0 ]; then
        export SDL_VIDEODRIVER=kmsdrm SDL_RENDER_DRIVER=software
    fi
    python3 -B "$MODULES/installer_ui.py" "$@" >> "$LOG" 2>&1 || fail "Graphical manager could not start or stopped unexpectedly. Check SDL2, SDL2_ttf and DejaVu fonts. Log: $LOG"
fi
