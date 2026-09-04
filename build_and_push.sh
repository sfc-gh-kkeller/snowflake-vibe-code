#!/bin/bash
# Build and push the devcontainer image to Snowflake registry
set -euo pipefail

# Configuration
DB="DEVCONTAINER_DB"
SCHEMA="SPCS"
REPO="IMAGES"

echo "==> Getting repository URL..."
REPO_URL=$(snow sql -q "SHOW IMAGE REPOSITORIES LIKE '${REPO}' IN SCHEMA ${DB}.${SCHEMA};" \
  --format json | jq -r '.[0].repository_url')

if [ -z "$REPO_URL" ]; then
  echo "ERROR: Could not find image repository. Run sql/01_infrastructure.sql first."
  exit 1
fi

echo "==> Repository URL: ${REPO_URL}"
echo "==> Logging into Snowflake image registry..."
snow spcs image-registry login

echo "==> Building image (linux/amd64)..."
docker build --platform linux/amd64 -t devcontainer:latest .

echo "==> Tagging and pushing..."
docker tag devcontainer:latest "${REPO_URL}/devcontainer:latest"
docker push "${REPO_URL}/devcontainer:latest"

echo "==> Done! Image pushed to: ${REPO_URL}/devcontainer:latest"
echo ""
echo "Next steps:"
echo "  1. Run sql/02_service.sql to deploy the service"
echo "  2. Run sql/03_service_functions.sql to create the UDFs"
echo "  3. Run sql/04_mcp_server.sql to create the MCP server"
echo "  4. Run sql/05_oauth_rbac.sql to configure auth"
echo "  5. Connect Claude Desktop to:"
echo "     https://<account_url>/api/v2/databases/DEVCONTAINER_DB/schemas/SPCS/mcp-servers/DEV_MCP_SERVER"
