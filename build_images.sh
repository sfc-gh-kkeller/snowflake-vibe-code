#!/bin/bash
# Build and push orchestrator and project-base images to Snowflake registry.
# Usage: ./build_images.sh [--connection <name>]
set -euo pipefail

CONNECTION="${1:---connection default}"

# Derive registry URL from snow CLI
REPO_URL=$(snow spcs image-registry url $CONNECTION 2>/dev/null | tr -d '[:space:]' || "")
if [ -z "$REPO_URL" ]; then
    echo "ERROR: Could not determine registry URL. Make sure 'snow' CLI is configured."
    echo "Usage: ./build_images.sh --connection <your_connection>"
    exit 1
fi
REPO_URL="${REPO_URL}/devcontainer_db/spcs/images"

echo "==> Registry: ${REPO_URL}"
echo "==> Logging into Snowflake image registry..."
snow spcs image-registry login $CONNECTION

echo ""
echo "==> Building orchestrator image..."
docker build --platform linux/amd64 -t orchestrator:latest ./orchestrator/
docker tag orchestrator:latest "${REPO_URL}/orchestrator:latest"
echo "==> Pushing orchestrator..."
docker push "${REPO_URL}/orchestrator:latest"

echo ""
echo "==> Building project-base image..."
docker build --platform linux/amd64 -t project-base:latest ./project_template/
docker tag project-base:latest "${REPO_URL}/project-base:latest"
echo "==> Pushing project-base..."
docker push "${REPO_URL}/project-base:latest"

echo ""
echo "==> Done! Both images pushed to ${REPO_URL}"
echo ""
echo "Next steps:"
echo "  1. Deploy infrastructure: snow sql -f sql/orchestrator_setup.sql $CONNECTION"
echo "  2. Connect your AI agent to the MCP server (see README.md)"
