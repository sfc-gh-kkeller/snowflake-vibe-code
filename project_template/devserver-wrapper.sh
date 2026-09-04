#!/bin/bash
# Dev server wrapper — starts the user's app on port 4000 if a start script exists.
# Supervisord manages this process with autorestart=unexpected, so:
#   - If the app crashes (non-zero exit), supervisord restarts it
#   - If no app exists (exit 0), supervisord leaves it stopped
#
# The AI agent creates start.sh when scaffolding a project, or the user can
# create it manually. It should exec the dev server (e.g. exec npm run dev).

set -e
cd /home/pixi/workspace

# Priority 1: explicit start.sh
if [ -x ./start.sh ]; then
    echo "[devserver] Found start.sh, launching..."
    exec ./start.sh
fi

# Priority 2: package.json with a "dev" script
if [ -f package.json ] && grep -q '"dev"' package.json 2>/dev/null; then
    echo "[devserver] Found package.json with dev script, running npm run dev..."
    export PORT="${PORT:-4000}"
    exec npm run dev
fi

# Priority 3: package.json with a "start" script
if [ -f package.json ] && grep -q '"start"' package.json 2>/dev/null; then
    echo "[devserver] Found package.json with start script, running npm start..."
    export PORT="${PORT:-4000}"
    exec npm start
fi

# Priority 4: Python app (app.py or main.py)
if [ -f app.py ]; then
    echo "[devserver] Found app.py, running with python..."
    exec python3 app.py
fi
if [ -f main.py ]; then
    echo "[devserver] Found main.py, running with python..."
    exec python3 main.py
fi

# No app found — wait and retry periodically (app may be created later by AI agent)
echo "[devserver] No start.sh, package.json dev/start script, or app.py found. Retrying in 30s..."
sleep 30
exec "$0"
