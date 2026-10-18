#!/bin/sh
# 227 AI - one-shot installer for the R36S (dArkOSRE, aarch64).
#
# 227 AI is a private, fully offline language model that runs on the handheld.
# This script downloads and installs everything it needs (inference runtime,
# the LFM 2.5 model, the speech-to-text engine, and the interface artwork),
# then registers a new EmulationStation system page called "227" so the app is
# launched like a game instead of from the Tools menu.
#
# Safe to re-run: verified files are kept, interrupted downloads resume, and
# the EmulationStation configuration is changed at most once (with a backup).
#
# Usage:
#   sh install.sh                 # normal install to /roms2
#   sh install.sh --dry-run       # show the plan without downloading anything
#   sh install.sh --prefix DIR    # install under DIR (testing)
#   sh install.sh --no-system     # do not touch the EmulationStation config
#   sh install.sh --es-cfg PATH   # use a specific es_systems.cfg
#   sh install.sh --themes DIR    # EmulationStation themes directory
#   sh install.sh --force         # re-download and overwrite existing files
#
# Environment overrides (mainly for development):
#   PROJECT_AI_RUNTIME_LOCAL=PATH   use a local runtime tarball instead of the
#                                   GitHub release (its .sha256 is used if it
#                                   sits next to the tarball).
#   PROJECT_AI_RAW_BASE=URL         override the raw file base (app/assets)
#   PROJECT_AI_REL_BASE=URL         override the release download base

set -eu

VERSION="1.0.0"
OWNER="1Z99"
REPO="project-ai-r36s"
REF="main"
RAW="${PROJECT_AI_RAW_BASE:-https://raw.githubusercontent.com/${OWNER}/${REPO}/${REF}}"
REL="${PROJECT_AI_REL_BASE:-https://github.com/${OWNER}/${REPO}/releases/latest/download}"

PREFIX="/roms2"
ES_CFG="/etc/emulationstation/es_systems.cfg"
THEMES=""
DRY_RUN=0
DO_SYSTEM=1
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)    PREFIX="$2"; shift 2 ;;
        --prefix=*)  PREFIX="${1#*=}"; shift ;;
        --es-cfg)    ES_CFG="$2"; shift 2 ;;
        --es-cfg=*)  ES_CFG="${1#*=}"; shift ;;
        --themes)    THEMES="$2"; shift 2 ;;
        --themes=*)  THEMES="${1#*=}"; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --no-system) DO_SYSTEM=0; shift ;;
        --force)     FORCE=1; shift ;;
        -h|--help)   awk 'NR>1 { if ($0 ~ /^set /) exit; print }' "$0"; exit 0 ;;
        *)           echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

TOOLS="${PREFIX}/tools"
ROOT="${TOOLS}/project_ai"
WHISPER_DIR="${ROOT}/whisper"
MODELS_DIR="${ROOT}/models"
LICENSE_DIR="${ROOT}/licenses"
SYSDIR="${PREFIX}/227ai"

detect_themes() {
    [ -n "$THEMES" ] && { echo "$THEMES"; return; }
    resolved="$(readlink -f /etc/emulationstation/themes 2>/dev/null)"
    for candidate in "$resolved" /roms/themes "${PREFIX}/themes"; do
        [ -n "$candidate" ] && [ -d "$candidate" ] && { echo "$candidate"; return; }
    done
    echo "/roms/themes"
}

THEMES="$(detect_themes)"

RUNTIME_ASSET="project-ai-r36s-runtime-arm64.tar.gz"
LAUNCHER_NAME="227 AI.sh"

LFM_FILE="LFM2.5-350M-QAD-Q4_0.gguf"
LFM_SHA="3d10b6ab8fc91a919534b9558e266255aca0bbc7f6d015963599aa9e74e05b1d"
LFM_HF="https://huggingface.co/LiquidAI/LFM2.5-350M-GGUF/resolve/main/${LFM_FILE}"

WHISPER_FILE="ggml-tiny.en-q5_1.bin"
WHISPER_SHA="c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b"
WHISPER_HF="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_FILE}"

APP_FILES="project_ai_r36s.py project_ai_settings.py"
ASSET_FILES="logo.rgba intro-frames.zip intro-background.rgba words.txt 227-art.jpg 227-poster.png 227-theme.xml"
LICENSE_FILES="llama.cpp-MIT.txt whisper.cpp-MIT.txt LFM2.5-Open-License.txt DejaVu-fonts-copyright espeak-ng-NOTICE.txt"

info() { printf '  %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }

have_curl=0
need curl && have_curl=1

# --- downloads -------------------------------------------------------------

# download one URL to dest (path without the .part suffix). Resumes if a
# previous .part file exists. Returns non-zero on failure.
download_try() {
    url="$1"; dest="$2"; part="${dest}.part"
    info "downloading $(basename "$dest")"
    if [ "$have_curl" -eq 1 ]; then
        if [ -s "$part" ]; then
            curl -fL --retry 3 --retry-delay 2 -C - -o "$part" "$url" || return 1
        else
            curl -fL --retry 3 --retry-delay 2 -o "$part" "$url" || return 1
        fi
    else
        wget -c -O "$part" "$url" || return 1
    fi
    mv -f "$part" "$dest"
}

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

verify() { # file expected-sha
    [ -f "$1" ] || return 1
    [ "$(sha_of "$1")" = "$2" ]
}

# download dest, verify against sha; try primary then optional fallback.
download_verified() { # dest sha primary_url [fallback_url]
    dest="$1"; sha="$2"; primary="$3"; fallback="${4:-}"
    if [ "$FORCE" -eq 0 ] && verify "$dest" "$sha"; then
        info "keep existing $(basename "$dest")"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would download $(basename "$dest")"
        return 0
    fi
    rm -f "$dest" "${dest}.part"
    if download_try "$primary" "$dest" && verify "$dest" "$sha"; then
        return 0
    fi
    warn "download from primary source failed for $(basename "$dest")"
    rm -f "$dest" "${dest}.part"
    if [ -n "$fallback" ]; then
        info "trying mirror..."
        if download_try "$fallback" "$dest" && verify "$dest" "$sha"; then
            return 0
        fi
    fi
    die "could not obtain a verified copy of $(basename "$dest")"
}

# fetch a small text file (no keep/resume semantics)
fetch_text() { # url dest
    if [ "$have_curl" -eq 1 ]; then
        curl -fsSL --retry 3 --retry-delay 2 -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

# --- preflight -------------------------------------------------------------

lib_present() { # soname-substring
    if need ldconfig && ldconfig -p 2>/dev/null | grep -q "$1"; then
        return 0
    fi
    [ -n "$(find /usr/lib /lib -maxdepth 2 -name "$1*" 2>/dev/null | head -n1)" ]
}

preflight() {
    echo "227 AI installer v${VERSION}"
    echo
    echo "Checking your device..."

    [ "$(id -u)" -eq 0 ] && warn "running as root; the app must run as the normal user" || true

    arch="$(uname -m)"
    if [ "$arch" = "aarch64" ]; then
        info "architecture: $arch"
    else
        warn "architecture is $arch, this build is for aarch64 (R36S). Continuing anyway."
    fi

    missing=""
    for tool in python3 sha256sum tar; do
        need "$tool" || missing="$missing $tool"
    done
    if [ "$have_curl" -eq 0 ] && ! need wget; then
        missing="$missing curl/wget"
    fi
    [ -n "$missing" ] && die "missing required tools:$missing"

    if [ ! -d "$PREFIX" ]; then
        die "$PREFIX not found. Insert the second SD card, or use --prefix."
    fi

    for lib in libSDL2-2.0.so.0 libSDL2_ttf-2.0.so.0 libespeak-ng.so.1; do
        if lib_present "$lib"; then
            info "found $lib"
        else
            warn "missing $lib (install: sudo apt-get install libsdl2-2.0-0 libsdl2-ttf-2.0-0 espeak-ng)"
        fi
    done
    for tool in arecord aplay; do
        need "$tool" && info "found $tool" || warn "missing $tool (microphone/audio may not work)"
    done

    free_kb="$(df -Pk "$PREFIX" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [ -n "${free_kb:-}" ] && [ "$free_kb" -lt 400000 ]; then
        warn "only $((free_kb / 1024)) MB free on $PREFIX; about 400 MB is needed"
    fi
    echo
}

# --- install steps ---------------------------------------------------------

make_dirs() {
    echo "Preparing folders..."
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would create $ROOT, $MODELS_DIR, $WHISPER_DIR, $SYSDIR"
        return 0
    fi
    mkdir -p "$ROOT" "$MODELS_DIR/whisper" "$WHISPER_DIR" "$LICENSE_DIR" "$SYSDIR"
}

install_runtime() {
    echo "Installing the inference runtime..."
    if [ "$FORCE" -eq 0 ] && [ -x "$ROOT/runtime/llama-server" ] && [ -x "$WHISPER_DIR/whisper-cli" ]; then
        info "runtime already installed"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would install runtime + whisper from GitHub release"
        return 0
    fi

    tar_file="${TMPDIR:-/tmp}/project-ai-runtime.$$.tar.gz"

    if [ -n "${PROJECT_AI_RUNTIME_LOCAL:-}" ]; then
        cp "$PROJECT_AI_RUNTIME_LOCAL" "$tar_file"
        if [ -f "${PROJECT_AI_RUNTIME_LOCAL}.sha256" ]; then
            want="$(cut -d' ' -f1 < "${PROJECT_AI_RUNTIME_LOCAL}.sha256")"
            verify "$tar_file" "$want" || die "local runtime checksum mismatch"
        else
            warn "no checksum next to local runtime; skipping verification"
        fi
    else
        sha_file="${TMPDIR:-/tmp}/project-ai-runtime.$$.sha256"
        fetch_text "${REL}/${RUNTIME_ASSET}.sha256" "$sha_file" \
            || die "could not fetch the runtime checksum (has the release been published?)"
        want="$(cut -d' ' -f1 < "$sha_file")"
        rm -f "$sha_file"
        download_try "${REL}/${RUNTIME_ASSET}" "$tar_file" || die "runtime download failed"
        verify "$tar_file" "$want" || { rm -f "$tar_file"; die "runtime checksum mismatch"; }
    fi

    tar -xzf "$tar_file" -C "$ROOT" || { rm -f "$tar_file"; die "could not extract the runtime"; }
    rm -f "$tar_file"
    chmod 755 "$ROOT/runtime/llama-server" "$WHISPER_DIR/whisper-cli" 2>/dev/null || true
    info "runtime ready"
}

install_models() {
    echo "Installing models (about 250 MB)..."
    download_verified "$MODELS_DIR/${LFM_FILE}" "$LFM_SHA" \
        "$LFM_HF" "${REL}/${LFM_FILE}"
    download_verified "$MODELS_DIR/whisper/${WHISPER_FILE}" "$WHISPER_SHA" \
        "$WHISPER_HF" "${REL}/${WHISPER_FILE}"
    if [ "$DRY_RUN" -eq 0 ] && [ -f "$LICENSE_DIR/LFM2.5-Open-License.txt" ]; then
        cp -f "$LICENSE_DIR/LFM2.5-Open-License.txt" "$MODELS_DIR/LICENSE-LFM2.5.txt"
    fi
}

install_files() { # raw-subdir dest-dir filenames...
    sub="$1"; dest="$2"; shift 2
    for name in "$@"; do
        url="${RAW}/${sub}/$(printf '%s' "$name" | sed 's/ /%20/g')"
        target="${dest}/${name}"
        if [ "$FORCE" -eq 0 ] && [ -f "$target" ]; then
            info "keep existing $name"
            continue
        fi
        if [ "$DRY_RUN" -eq 1 ]; then
            info "would fetch $url"
            continue
        fi
        info "fetching $name"
        fetch_text "$url" "$target" || die "could not download $name"
    done
}

install_code() {
    echo "Installing application and artwork..."
    install_files app "$TOOLS" $APP_FILES
    install_files assets "$ROOT" $ASSET_FILES
    install_files licenses "$LICENSE_DIR" $LICENSE_FILES
}

install_system() {
    echo "Creating the EmulationStation system page..."
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would create $SYSDIR/${LAUNCHER_NAME} and gamelist.xml"
        return 0
    fi
    install_files system "$SYSDIR" "${LAUNCHER_NAME}" gamelist.xml
    if [ -f "$ROOT/227-art.jpg" ]; then
        cp -f "$ROOT/227-art.jpg" "$SYSDIR/227-art.jpg"
    fi
    chmod 755 "$SYSDIR/${LAUNCHER_NAME}" 2>/dev/null || true
}

# Give the 227 system a name and artwork in every installed EmulationStation
# theme. The name comes from a per-system theme.xml folder; the photo comes
# from a poster image (themes that read _art/posters/${system.theme}).
install_theme() {
    [ "$DO_SYSTEM" -eq 1 ] || return 0
    echo "Adding 227 artwork to EmulationStation themes..."
    if [ ! -d "$THEMES" ]; then
        warn "no themes directory at $THEMES (skipping system artwork)"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would add 227 name + poster to themes in $THEMES"
        return 0
    fi

    template="$ROOT/227-theme.xml"
    poster="$ROOT/227-poster.png"
    count=0
    for theme in "$THEMES"/*/; do
        [ -f "${theme}theme.xml" ] || continue
        fmt="$(grep -o 'formatVersion>[0-9]*' "${theme}theme.xml" 2>/dev/null | head -n1 | grep -o '[0-9]*')"
        [ -n "$fmt" ] || fmt=7
        mkdir -p "${theme}227"
        if [ -f "$template" ]; then
            sed "s|<formatVersion>[0-9]*</formatVersion>|<formatVersion>${fmt}</formatVersion>|" \
                "$template" > "${theme}227/theme.xml"
        fi
        if [ -d "${theme}_art/posters" ] && [ -f "$poster" ]; then
            cp -f "$poster" "${theme}_art/posters/227.png"
        fi
        count=$((count + 1))
    done
    info "added 227 name + artwork to $count theme(s)"
}

write_config() {
    if [ -f "$ROOT/config.json" ] && [ "$FORCE" -eq 0 ]; then
        info "keeping your existing settings"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would write a default config.json"
        return 0
    fi
    cat > "$ROOT/config.json" <<'JSON'
{
  "model": "fast",
  "context": 768,
  "max_tokens": 160,
  "temperature": 0.25,
  "highlight": true,
  "speech_rate": 1.0,
  "background": 20,
  "intro": true
}
JSON
    info "wrote default settings (model: LFM 2.5)"
}

patch_es() {
    [ "$DO_SYSTEM" -eq 1 ] || { info "not touching the EmulationStation config (--no-system)"; return 0; }
    if [ "$DRY_RUN" -eq 1 ]; then
        info "would register system 227 in $ES_CFG"
        return 0
    fi
    if [ ! -f "$ES_CFG" ]; then
        warn "no EmulationStation config at $ES_CFG (skipping registration)"
        return 0
    fi

    py="${TMPDIR:-/tmp}/patch-es-227.$$.py"
    cat > "$py" <<'PY'
import os, shutil, sys
path = sys.argv[1]
block = (
    '\t<system>\n'
    '\t\t<name>227</name>\n'
    '\t\t<fullname>227</fullname>\n'
    '\t\t<path>/roms2/227ai/</path>\n'
    '\t\t<extension>.sh .SH</extension>\n'
    '\t\t<command>%ROM%</command>\n'
    '\t\t<platform>227</platform>\n'
    '\t\t<theme>227</theme>\n'
    '\t\t<manufacturer>1Z99</manufacturer>\n'
    '\t\t<release>2026</release>\n'
    '\t</system>\n'
)
with open(path, encoding='utf-8', errors='replace') as fh:
    data = fh.read()
if '<name>227</name>' in data:
    print('  system 227 is already registered')
    sys.exit(0)
if not os.path.exists(path + '.pre-227ai'):
    shutil.copy2(path, path + '.pre-227ai')
    print('  backed up to ' + path + '.pre-227ai')
index = data.rfind('</systemList>')
if index == -1:
    print('  ERROR: could not find </systemList> in ' + path)
    sys.exit(2)
tmp = path + '.new'
with open(tmp, 'w', encoding='utf-8') as fh:
    fh.write(data[:index] + block + data[index:])
os.replace(tmp, path)
print('  registered system 227 in ' + path)
PY

    if [ -w "$ES_CFG" ]; then
        python3 "$py" "$ES_CFG"
    elif need sudo; then
        info "need administrator rights to update $ES_CFG"
        sudo python3 "$py" "$ES_CFG"
    else
        warn "cannot write $ES_CFG and sudo is unavailable; skipping registration"
    fi
    rm -f "$py"
}

summary() {
    echo
    echo "==================================================================="
    if [ "$DRY_RUN" -eq 1 ]; then
        echo " Dry run complete - nothing was written."
        echo "==================================================================="
        return 0
    fi
    echo " 227 AI is installed."
    echo "==================================================================="
    echo
    echo " Restart EmulationStation (or reboot) to see the new \"227\" system,"
    echo " then open it and choose \"227 AI\"."
    echo
    echo "  App scripts : $TOOLS/project_ai_r36s.py"
    echo "  Data        : $ROOT"
    echo "  System page : $SYSDIR"
    echo "  Settings    : the R2 button inside the app"
    echo
}

main() {
    preflight
    make_dirs
    install_runtime
    install_models
    install_code
    install_system
    install_theme
    write_config
    patch_es
    summary
}

main "$@"
