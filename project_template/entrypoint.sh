#!/bin/bash
set -e

INIT_MARKER="/home/pixi/.initialized"

if [ ! -f "$INIT_MARKER" ]; then
    echo "Initializing home directory..."

    # Copy skeleton (pixi, asdf, bashrc)
    cp -rn /etc/skel.pixi/. /home/pixi/ 2>/dev/null || true

    mkdir -p /home/pixi/workspace
    chown -R pixi:pixi /home/pixi

    touch "$INIT_MARKER"
    echo "Home directory initialized."
fi

# Ensure nginx temp dirs exist
mkdir -p /tmp/nginx/client_body /tmp/nginx/proxy /tmp/nginx/fastcgi /tmp/nginx/uwsgi /tmp/nginx/scgi

# Ensure supervisord include dir exists
mkdir -p /etc/supervisord.d

# Dev mode gating: start VSCode/terminal/token-refresh only if enabled AND allowed
if [ "${DEVMODE_ENABLED:-false}" = "true" ] && [ "${DEVMODE_ALLOWED:-true}" != "false" ]; then
    echo "[entrypoint] Dev mode ENABLED: starting VSCode, terminal, token-refresh"
    cp /opt/api/supervisord-devmode.conf /etc/supervisord.d/devmode.conf
else
    echo "[entrypoint] Dev mode DISABLED: MCP-only mode (no VSCode, terminal, token-refresh)"
    rm -f /etc/supervisord.d/devmode.conf
fi

exec "$@"
