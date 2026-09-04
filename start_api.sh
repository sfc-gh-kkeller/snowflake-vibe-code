#!/bin/bash
# Startup script for the devcontainer API server
# Runs alongside supervisord processes

# Install FastAPI dependencies
pip install --quiet fastapi uvicorn 2>/dev/null || \
  /home/pixi/.pixi/bin/pip install --quiet fastapi uvicorn

# Start the API server
cd /home/pixi/api_server
exec uvicorn main:app --host 0.0.0.0 --port 8080
