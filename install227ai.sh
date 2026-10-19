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
        'Handheld install/update sets up missing dependencies; --desktop skips system/font setup.' \
        'Development: PROJECT_AI_RAW_BASE, PROJECT_AI_REL_BASE, PROJECT_AI_RUNTIME_LOCAL.'
}

CLI=0 DRY=0 YES=0 DESKTOP=0 SOURCE_SET=0 CLI_FLAGS=0
ROMS='' ACTION='' SOURCE='' THEMES=''
ES_CFG=/etc/emulationstation/es_systems.cfg
LOG='' TEMP='' FONT_TEMP=''
fail() {
    printf '227 AI: %s\n' "$*" >&2
    if [ -n "$LOG" ]; then
        printf '227 AI: %s\n' "$*" >> "$LOG" 2>/dev/null || true
        printf 'Log: %s\n' "$LOG" >&2
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
# Only an actual installation request may bootstrap missing system tools.
AUTO=0 MISSING=''
if [ "$DRY" -eq 0 ] && [ "$DESKTOP" -eq 0 ] && [ "$ACTION" != uninstall ]; then AUTO=1; fi
if command -v python3 >/dev/null 2>&1; then
    python3 -B -c 'import sys; sys.exit(sys.version_info < (3, 9))' </dev/null 2>/dev/null ||
        fail 'Existing Python is broken or older than 3.9; it will not be replaced. Repair it manually.'
else
    [ "$AUTO" -eq 1 ] || fail 'Python 3.9 or newer is required; automatic setup is disabled for this mode.'
    MISSING=python3
fi
if [ "$AUTO" -eq 1 ] && ! command -v curl >/dev/null 2>&1; then MISSING="${MISSING:+$MISSING }curl"; fi
bootstrap_tools() {
    MANUAL="Missing packages: $MISSING. Manual setup: sudo apt-get --no-upgrade --no-remove --no-install-recommends install $MISSING"
    FAMILY=$(awk -F= '$1 == "ID" || $1 == "ID_LIKE" {gsub(/[\047\042]/, "", $2); printf " %s ", tolower($2)}' "/etc/os-release" 2>/dev/null) || fail "$MANUAL; cannot identify OS."
    case "$FAMILY" in *' debian '*|*' ubuntu '*) ;; *) fail "$MANUAL; automatic setup supports only Debian/Ubuntu." ;; esac
    [ "$(uname -m)" = aarch64 ] || fail "$MANUAL; automatic setup requires aarch64."
    APT=$(command -v apt-get) && DPKG=$(command -v dpkg) || fail "$MANUAL; apt-get and dpkg are required."
    [ "$(LC_ALL=C "$DPKG" --print-architecture)" = arm64 ] || fail "$MANUAL; dpkg architecture must be arm64."
    SUDO=''
    if [ "$(id -u)" != 0 ]; then
        SUDO=$(command -v sudo) || fail "$MANUAL; root or noninteractive sudo -n is required."
        "$SUDO" -n -- true </dev/null 2>/dev/null || fail "$MANUAL; sudo -n denied. No password is requested or stored."
    fi
    SETUP=$(mktemp -d "/tmp/227-ai-setup.XXXXXX") || fail 'Cannot create setup log directory.'
    LOG="$SETUP/setup.log"
    printf 'Setting up missing tools: %s\nLog: %s\n' "$MISSING" "$LOG"
    setup_apt() {
        set -- "$APT" -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 \
            -o Acquire::Retries=1 -o DPkg::Lock::Timeout=30 "$@"
        if [ -n "$SUDO" ]; then
            set -- "$SUDO" -n -- /usr/bin/env DEBIAN_FRONTEND=noninteractive LC_ALL=C "$@"
        fi
        : > "$SETUP/output"
        # Preserve apt's status across tee without requiring bash/pipefail.
        (if env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive LC_ALL=C \
            "$@" </dev/null; then printf 0 > "$SETUP/status"; else printf '%s' "$?" > "$SETUP/status"; fi) \
            2>&1 | tee -a "$SETUP/output" "$LOG"
        [ "$(cat "$SETUP/status")" = 0 ]
    }
    audit_plan() {
        awk -v required="$MISSING" '
        /^Remv([[:space:]]|$)/ {bad=1}
        /^Inst[[:space:]]/ {
            name=$2; version=$3; base=name; sub(/:.*/, "", base)
            if (name !~ /^[a-z0-9][a-z0-9+.-]*(:[a-z0-9-]+)?$/ ||
                version !~ /^\(/ || $0 !~ /\)$/ || seen[name]++ ||
                base ~ /^(mesa|libegl|libgles|libgl|libgbm|libdrm|libmali|xserver|firmware|libsdl2)/ ||
                (base ~ /^(python3|libpython3)/ && required !~ /(^| )python3( |$)/)) {bad=1; next}
            sub(/^\(/, "", version); sub(/\)$/, "", version)
            if (version !~ /^[A-Za-z0-9.+:~\055]+$/) {bad=1; next}
            found[base]=1; print name "=" version
        }
        END {n=split(required, names, " "); for (i=1;i<=n;i++) if (!found[names[i]]) bad=1; if (bad) exit 1}
        ' "$SETUP/output" > "$1" || fail "Unsafe apt plan: upgrades, removals, graphics/firmware or missing tools. $MANUAL"
    }
    # MISSING and the audited pin files contain only validated package tokens.
    if ! setup_apt --simulate --no-upgrade --no-remove --no-install-recommends install $MISSING; then
        printf '%s\n' 'Refreshing package lists...'
        setup_apt update || fail "Cannot refresh package lists. $MANUAL"
        setup_apt --simulate --no-upgrade --no-remove --no-install-recommends install $MISSING || fail "Cannot plan setup. $MANUAL"
    fi
    audit_plan "$SETUP/pinned"
    while IFS='=' read -r name version; do
        case "${name%%:*}" in python3)
            "$DPKG" --compare-versions "$version" ge 3.9 || fail 'Python candidate is older than 3.9; refusing setup.' ;;
        esac
    done < "$SETUP/pinned"
    sort "$SETUP/pinned" > "$SETUP/approved"
    set -- $(cat "$SETUP/approved")
    setup_apt --simulate --no-upgrade --no-remove --no-install-recommends install "$@" || fail 'Cannot verify pinned setup plan.'
    audit_plan "$SETUP/pinned"
    sort "$SETUP/pinned" > "$SETUP/verified"
    cmp -s "$SETUP/approved" "$SETUP/verified" || fail 'Apt plan changed; refusing setup.'
    printf '%s\n' 'Installing missing tools; let system setup finish safely...'
    setup_apt --yes --no-upgrade --no-remove --no-install-recommends install "$@" || fail 'System tool setup failed.'
    command -v python3 >/dev/null 2>&1 && python3 -B -c 'import sys; sys.exit(sys.version_info < (3, 9))' </dev/null || fail 'Python 3.9+ still unavailable after setup.'
    command -v curl >/dev/null 2>&1 || fail 'curl still unavailable after setup.'
}
[ -z "$MISSING" ] || bootstrap_tools
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

if [ "$ACTION" = uninstall ]; then
    [ -n "$MODULES" ] && [ -f "$MODULES/installer_cli.py" ] || fail 'Uninstall needs an existing manager; no tools were installed or downloaded.'
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
for name in ('bootstrap.log', 'install.sh', 'install.bootstrap', 'installer_ui.py', 'installer_core.py', 'installer_cli.py', 'installer_deps.py'):
    if (path / name).is_symlink():
        sys.exit('Refusing symlink manager file: ' + name)
    if name in ('install.sh', 'install.bootstrap') and (path / name).exists() and not (path / name).is_file():
        sys.exit('Refusing non-regular manager file: ' + name)
PY
    mkdir -p "$MODULES" || fail 'Cannot create manager directory.'
    LOG="$MODULES/bootstrap.log"
fi
: >> "$LOG" || fail "Cannot write $LOG"
trap '[ -z "$TEMP" ] || rm -rf -- "$TEMP"; [ -z "$FONT_TEMP" ] || rm -rf -- "$FONT_TEMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ "$LOCAL_REPO" -eq 0 ]; then
    TEMP=$(mktemp -d "$MODULES/.bootstrap.XXXXXX") || fail 'Cannot create bootstrap staging directory.'
    if [ "$ACTION" != uninstall ] && { [ ! -f "$MODULES/installer_ui.py" ] || [ ! -f "$MODULES/installer_core.py" ] || [ ! -f "$MODULES/installer_cli.py" ] || [ ! -f "$MODULES/installer_deps.py" ]; }; then
        command -v curl >/dev/null 2>&1 || fail 'curl is required to download the manager.'
        RAW=${PROJECT_AI_RAW_BASE:-https://raw.githubusercontent.com/1Z99/project-ai-r36s/main}
        for name in installer_core.py installer_ui.py installer_cli.py installer_deps.py; do
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
        for name in installer_core.py installer_ui.py installer_cli.py installer_deps.py; do
            mv -f -- "$TEMP/$name" "$MODULES/$name" || fail "Cannot publish $name."
        done
    fi
    # EmulationStation scans subfolders for .sh files; keep internal code off the menu.
    if [ "$SELF" != "$MODULES/install.bootstrap" ]; then
        cp -- "$SELF" "$TEMP/install.bootstrap" && chmod 755 "$TEMP/install.bootstrap" && \
            mv -f -- "$TEMP/install.bootstrap" "$MODULES/install.bootstrap" || fail 'Cannot retain bootstrap.'
    fi
    ENTRY="$(dirname -- "$MODULES")/Install 227 AI.sh"
    [ ! -L "$ENTRY" ] || fail 'Refusing symlink Tools entry.'
    [ ! -e "$ENTRY" ] || [ -f "$ENTRY" ] || fail 'Refusing non-regular Tools entry.'
    printf '%s\n' '#!/bin/sh' \
        'DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)' \
        'exec sh "$DIR/227-ai-manager/install.bootstrap" "$@"' > "$TEMP/entry.sh"
    chmod 755 "$TEMP/entry.sh" && mv -f -- "$TEMP/entry.sh" "$ENTRY" || fail 'Cannot publish Tools entry.'
    # Remove the legacy menu entry only after its replacement and shortcut are ready.
    rm -f -- "$MODULES/install.sh" || fail 'Cannot remove legacy manager menu entry.'
fi

if [ "$CLI" -eq 1 ]; then
    python3 -B "$MODULES/installer_cli.py" "$@"
else
    if [ "$DESKTOP" -eq 0 ]; then
        python3 -B "$MODULES/installer_deps.py" --scope manager --install >> "$LOG" 2>&1 || {
            tail -n 20 "$LOG" >&2
            fail "Manager dependency setup failed. Log: $LOG"
        }
        FONT_TEMP=$(mktemp -d "$MODULES/.fonts.XXXXXX") || fail 'Cannot stage bundled fonts.'
        python3 -B - "$MODULES" "$FONT_TEMP" "$SELF_DIR" "$SOURCE" \
            "${PROJECT_AI_RAW_BASE:-https://raw.githubusercontent.com/1Z99/project-ai-r36s/main}" <<'PY' >> "$LOG" 2>&1 || {
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

modules, stage, local = map(Path, sys.argv[1:4])
source, raw = sys.argv[4:6]
fonts = modules / 'fonts'
for path in (fonts,) + tuple(fonts.parents):
    if path.is_symlink():
        raise ValueError('Refusing symlink font storage: ' + str(path))
files = {'DejaVuSansMono.ttf': '54bf827eb99404e8f430c330ad30f063334f637eba0109b6a18d4f566a8e9dd8',
         'DejaVuSansMono-Bold.ttf': '0d3c03d1b667192f91223660a3163325cf83132662fe4d9f7d6e596bf7a995c2',
         'DejaVu-fonts-copyright': '63d3ba759d12804c5b31a9d5940d855c1820d1f5999e6b0872eb1c7ff045fbc9'}
for name in files:
    if (fonts / name).is_symlink():
        raise ValueError('Refusing symlink font file: ' + name)

def valid(path, digest):
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest

roots = ([Path(source)] if source else []) + [local, local.parent, modules / 'bundle']
for name, digest in files.items():
    target = fonts / name
    if valid(target, digest):
        continue
    relative = ('licenses/' if name == 'DejaVu-fonts-copyright' else 'assets/') + name
    staged = stage / name
    candidate = next((root / relative for root in roots if valid(root / relative, digest)), None)
    if candidate is not None:
        shutil.copyfile(candidate, staged)
    else:
        print('Fetching bundled font asset: ' + name, flush=True)
        subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                        '--connect-timeout', '15', '--speed-time', '30', '--speed-limit', '1024',
                        '--max-time', '120', '--retry', '2', '--retry-delay', '2',
                        '--output', str(staged), raw.rstrip('/') + '/' + relative], check=True, timeout=400)
    if not valid(staged, digest):
        raise ValueError('Bundled font checksum mismatch: ' + name)
    fonts.mkdir(exist_ok=True)
    os.replace(staged, target)
PY
            tail -n 20 "$LOG" >&2
            fail "Cannot prepare bundled manager fonts. Log: $LOG"
        }
        export SDL_VIDEODRIVER=kmsdrm SDL_RENDER_DRIVER=software
    fi
    python3 -B "$MODULES/installer_ui.py" "$@" >> "$LOG" 2>&1 || fail "Graphical manager could not start or stopped unexpectedly. Check SDL2, SDL2_ttf and DejaVu fonts. Log: $LOG"
fi
