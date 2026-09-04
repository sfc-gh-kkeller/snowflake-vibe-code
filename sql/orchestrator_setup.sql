-- =============================================================================
-- SPCS Orchestrator: Full Infrastructure Setup
-- Single SQL file that sets up everything for the multi-tenant vibe-code system
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- =============================================================================
-- 1. DATABASE AND SCHEMAS
-- =============================================================================
CREATE DATABASE IF NOT EXISTS DEVCONTAINER_DB;
CREATE SCHEMA IF NOT EXISTS DEVCONTAINER_DB.SPCS;      -- orchestrator lives here
CREATE SCHEMA IF NOT EXISTS DEVCONTAINER_DB.PROJECTS;  -- per-project services live here

-- =============================================================================
-- 2. IMAGE REPOSITORIES
-- =============================================================================
CREATE IMAGE REPOSITORY IF NOT EXISTS DEVCONTAINER_DB.SPCS.IMAGES;

-- =============================================================================
-- 3. COMPUTE POOLS
-- =============================================================================
-- Orchestrator pool (always on, tiny)
CREATE COMPUTE POOL IF NOT EXISTS ORCHESTRATOR_POOL
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_SUSPEND_SECS = 3600
  AUTO_RESUME = TRUE;

-- Project pool (auto-scales with demand)
CREATE COMPUTE POOL IF NOT EXISTS PROJECT_POOL
  MIN_NODES = 1
  MAX_NODES = 10
  INSTANCE_FAMILY = CPU_X64_S
  AUTO_SUSPEND_SECS = 900
  AUTO_RESUME = TRUE;

-- =============================================================================
-- 4. WAREHOUSE
-- =============================================================================
CREATE WAREHOUSE IF NOT EXISTS DEVCONTAINER_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;

-- =============================================================================
-- 5. NETWORK ACCESS (for package managers in project containers)
-- =============================================================================
CREATE NETWORK RULE IF NOT EXISTS DEVCONTAINER_DB.SPCS.EXTERNAL_ACCESS_RULE
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = ('0.0.0.0:80', '0.0.0.0:443');

CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS DEVCONTAINER_EXTERNAL_ACCESS
  ALLOWED_NETWORK_RULES = (DEVCONTAINER_DB.SPCS.EXTERNAL_ACCESS_RULE)
  ENABLED = TRUE;

-- =============================================================================
-- 6. PROJECT REGISTRY TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS DEVCONTAINER_DB.PROJECTS.REGISTRY (
    project_id VARCHAR(64) PRIMARY KEY,
    project_name VARCHAR(256),
    owner_username VARCHAR(256),
    secret_hash VARCHAR(256),
    service_name VARCHAR(256),
    service_dns VARCHAR(512),
    public_url VARCHAR(1024),
    template VARCHAR(64),
    status VARCHAR(32) DEFAULT 'CREATING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
);

-- =============================================================================
-- 7. ORCHESTRATOR SERVICE
-- =============================================================================
CREATE SERVICE IF NOT EXISTS DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
  IN COMPUTE POOL ORCHESTRATOR_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: orchestrator
      image: /devcontainer_db/spcs/images/orchestrator:latest
      env:
        API_PORT: "8080"
        SNOWFLAKE_HOST: ""
        PROJECT_SCHEMA: "DEVCONTAINER_DB.PROJECTS"
        PROJECT_POOL: "PROJECT_POOL"
        PROJECT_IMAGE: "/devcontainer_db/spcs/images/project-base:latest"
        QUERY_WAREHOUSE: "DEVCONTAINER_WH"
      resources:
        requests:
          memory: 512Mi
          cpu: 500m
        limits:
          memory: 1Gi
          cpu: 1000m
      readinessProbe:
        port: 8080
        path: /api/health
  endpoints:
    - name: api
      port: 8080
$$
  EXTERNAL_ACCESS_INTEGRATIONS = (DEVCONTAINER_EXTERNAL_ACCESS)
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1;

-- =============================================================================
-- 8. SERVICE FUNCTIONS (all point to orchestrator)
-- =============================================================================

-- Project management
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.CREATE_PROJECT(
    project_name VARCHAR,
    template VARCHAR DEFAULT 'nextjs'
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/create_project';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.LIST_MY_PROJECTS()
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/list_projects';

-- Project operations (require project_id + secret)
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_EXEC(
    command VARCHAR,
    project_id VARCHAR,
    secret VARCHAR,
    timeout_seconds NUMBER DEFAULT 30
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/exec';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_READ(
    file_path VARCHAR,
    project_id VARCHAR,
    secret VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/read';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_WRITE(
    file_path VARCHAR,
    content VARCHAR,
    project_id VARCHAR,
    secret VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/write';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_EDIT(
    file_path VARCHAR,
    old_string VARCHAR,
    new_string VARCHAR,
    project_id VARCHAR,
    secret VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/edit';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_LIST(
    directory VARCHAR,
    project_id VARCHAR,
    secret VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/list';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_INSTALL(
    manager VARCHAR,
    packages VARCHAR,
    project_id VARCHAR,
    secret VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/project/install';

-- Admin operations
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.ADMIN_ROTATE_SECRET(
    project_id VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/admin/rotate_secret';

CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.ADMIN_DESTROY_PROJECT(
    project_id VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.ORCHESTRATOR_SERVICE
ENDPOINT = api
AS '/api/admin/destroy';

-- =============================================================================
-- 9. MCP SERVER (single, shared by all users)
-- =============================================================================
CREATE OR REPLACE MCP SERVER DEVCONTAINER_DB.SPCS.VIBE_CODE_MCP
FROM SPECIFICATION $$
tools:
  - title: "Create Project"
    name: "create_project"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.CREATE_PROJECT"
    description: "Create a new dev container project. Returns a project_id and secret that you must keep for all subsequent operations. Templates: nextjs, react, python."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          project_name:
            type: "string"
            description: "Name for the project"
          template:
            type: "string"
            description: "Template: nextjs, react, or python (default: nextjs)"

  - title: "List My Projects"
    name: "list_my_projects"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.LIST_MY_PROJECTS"
    description: "List all your projects with their IDs and status."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"

  - title: "Run Command"
    name: "project_exec"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_EXEC"
    description: "Execute a shell command in a project container. Requires project_id and secret."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          command:
            type: "string"
            description: "Shell command to execute"
          project_id:
            type: "string"
            description: "Project ID (from create_project)"
          secret:
            type: "string"
            description: "Project secret (from create_project)"
          timeout_seconds:
            type: "number"
            description: "Timeout in seconds (default 30)"

  - title: "Read File"
    name: "project_read"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_READ"
    description: "Read a file from a project container."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            type: "string"
            description: "Absolute path in the container"
          project_id:
            type: "string"
            description: "Project ID"
          secret:
            type: "string"
            description: "Project secret"

  - title: "Write File"
    name: "project_write"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_WRITE"
    description: "Create or overwrite a file in a project container."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            type: "string"
            description: "Absolute file path"
          content:
            type: "string"
            description: "File content"
          project_id:
            type: "string"
            description: "Project ID"
          secret:
            type: "string"
            description: "Project secret"

  - title: "Edit File"
    name: "project_edit"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_EDIT"
    description: "Find and replace text in a file. old_string must be unique in the file."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            type: "string"
            description: "File path"
          old_string:
            type: "string"
            description: "Text to find (must be unique)"
          new_string:
            type: "string"
            description: "Replacement text"
          project_id:
            type: "string"
            description: "Project ID"
          secret:
            type: "string"
            description: "Project secret"

  - title: "List Files"
    name: "project_list"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_LIST"
    description: "List files in a project container directory."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          directory:
            type: "string"
            description: "Directory to list (default /workspace)"
          project_id:
            type: "string"
            description: "Project ID"
          secret:
            type: "string"
            description: "Project secret"

  - title: "Install Package"
    name: "project_install"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.PROJECT_INSTALL"
    description: "Install packages in a project container. Supports npm, pip, pixi."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          manager:
            type: "string"
            description: "Package manager: npm, pip, or pixi"
          packages:
            type: "string"
            description: "Space-separated package names"
          project_id:
            type: "string"
            description: "Project ID"
          secret:
            type: "string"
            description: "Project secret"

  - title: "Execute SQL"
    name: "sql_exec"
    type: "SYSTEM_EXECUTE_SQL"
    description: "Run SQL against Snowflake as the authenticated user."
    config:
      read_only: false
      query_timeout: 600
      warehouse: "DEVCONTAINER_WH"
$$;

-- =============================================================================
-- 10. RBAC
-- =============================================================================
CREATE ROLE IF NOT EXISTS VIBE_CODE_USER;

GRANT USAGE ON DATABASE DEVCONTAINER_DB TO ROLE VIBE_CODE_USER;
GRANT USAGE ON SCHEMA DEVCONTAINER_DB.SPCS TO ROLE VIBE_CODE_USER;
GRANT USAGE ON SCHEMA DEVCONTAINER_DB.PROJECTS TO ROLE VIBE_CODE_USER;
GRANT USAGE ON WAREHOUSE DEVCONTAINER_WH TO ROLE VIBE_CODE_USER;

-- UDF grants
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.CREATE_PROJECT(VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.LIST_MY_PROJECTS() TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_EXEC(VARCHAR, VARCHAR, VARCHAR, NUMBER) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_READ(VARCHAR, VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_WRITE(VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_EDIT(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_LIST(VARCHAR, VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;
GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.PROJECT_INSTALL(VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO ROLE VIBE_CODE_USER;

-- MCP server access
GRANT USAGE ON MCP SERVER DEVCONTAINER_DB.SPCS.VIBE_CODE_MCP TO ROLE VIBE_CODE_USER;

-- Admin-only functions (do not grant to regular users)
-- GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.ADMIN_ROTATE_SECRET(VARCHAR) TO ROLE VIBE_CODE_ADMIN;
-- GRANT USAGE ON FUNCTION DEVCONTAINER_DB.SPCS.ADMIN_DESTROY_PROJECT(VARCHAR) TO ROLE VIBE_CODE_ADMIN;

-- Grant role to users
GRANT ROLE VIBE_CODE_USER TO ROLE ACCOUNTADMIN;
-- GRANT ROLE VIBE_CODE_USER TO USER "your_user_here";

-- =============================================================================
-- 11. OAUTH (for Claude Desktop / Cowork)
-- =============================================================================
CREATE OR REPLACE SECURITY INTEGRATION VIBE_CODE_MCP_OAUTH
  TYPE = OAUTH
  OAUTH_CLIENT = CUSTOM
  OAUTH_CLIENT_TYPE = 'CONFIDENTIAL'
  OAUTH_REDIRECT_URI = 'https://claude.ai/api/mcp/auth_callback'
  OAUTH_ISSUE_REFRESH_TOKENS = TRUE
  OAUTH_USE_SECONDARY_ROLES = NONE
  ALLOWED_ROLES_LIST = ('VIBE_CODE_USER')
  ENABLED = TRUE;

-- Get credentials:
-- SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('VIBE_CODE_MCP_OAUTH');
