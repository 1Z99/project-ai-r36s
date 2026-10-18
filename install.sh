#!/bin/sh
# Compatibility entry for existing links; new downloads use install227ai.sh.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -f "$DIR/install227ai.sh" ]; then
    exec sh "$DIR/install227ai.sh" "$@"
fi
TMP=$(mktemp -d /tmp/227-bootstrap.XXXXXX)
trap 'rm -rf -- "$TMP"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl --fail --location --show-error --connect-timeout 15 \
    --speed-time 30 --speed-limit 1024 --max-time 120 \
    -o "$TMP/install227ai.sh" \
    https://raw.githubusercontent.com/1Z99/project-ai-r36s/main/install227ai.sh
sh "$TMP/install227ai.sh" "$@"
