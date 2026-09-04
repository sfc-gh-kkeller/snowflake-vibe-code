-- =============================================================================
-- SPCS DevContainer: OAuth and RBAC
-- Security integration for Claude Desktop/Cowork and role grants
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- -----------------------------------------------------------------------------
-- Role for MCP access
-- -----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS DEVCONTAINER_USER;
GRANT ROLE DEVCONTAINER_USER TO ROLE SYSADMIN;

-- Database-level access
GRANT USAGE ON DATABASE DEVCONTAINER_DB TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON SCHEMA DEVCONTAINER_DB.SPCS TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON WAREHOUSE DEVCONTAINER_WH TO ROLE DEVCONTAINER_USER;

-- MCP server access
GRANT USAGE ON MCP SERVER DEVCONTAINER_DB.SPCS.DEV_MCP_SERVER TO ROLE DEVCONTAINER_USER;

-- Service function access (all tools)
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.EXEC_COMMAND(VARCHAR, VARCHAR, NUMBER)
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.READ_FILE(VARCHAR, NUMBER, NUMBER)
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.WRITE_FILE(VARCHAR, VARCHAR)
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.EDIT_FILE(VARCHAR, VARCHAR, VARCHAR)
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.LIST_FILES(VARCHAR, VARCHAR)
  TO ROLE DEVCONTAINER_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.INSTALL_PACKAGE(VARCHAR, VARCHAR)
  TO ROLE DEVCONTAINER_USER;

-- Service role grant (for endpoint access)
GRANT SERVICE ROLE DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE!api_user
  TO ROLE DEVCONTAINER_USER;

-- Grant the Cortex agent user database role (required for MCP)
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_AGENT_USER TO ROLE DEVCONTAINER_USER;

-- -----------------------------------------------------------------------------
-- OAuth Security Integration for Claude Desktop / Cowork
-- -----------------------------------------------------------------------------
CREATE OR REPLACE SECURITY INTEGRATION DEVCONTAINER_MCP_OAUTH
  TYPE = OAUTH
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'https://claude.ai/api/mcp/auth_callback'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_USE_SECONDARY_ROLES = NONE
  ALLOWED_ROLES_LIST = ('DEVCONTAINER_USER')
  ENABLED = TRUE;

-- Retrieve client credentials (run after creation):
-- SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('DEVCONTAINER_MCP_OAUTH');

-- -----------------------------------------------------------------------------
-- Assign role to users
-- -----------------------------------------------------------------------------
-- Grant to specific users:
-- GRANT ROLE DEVCONTAINER_USER TO USER "<YOUR_USER>";

-- Set as default role for MCP session (Claude uses DEFAULT_ROLE):
-- ALTER USER "<YOUR_USER>"
--   SET DEFAULT_ROLE = 'DEVCONTAINER_USER'
--       DEFAULT_WAREHOUSE = 'DEVCONTAINER_WH';
