-- =============================================================================
-- SPCS DevContainer: Service Functions
-- UDFs that bridge MCP tool calls to the container's HTTP API
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE DEVCONTAINER_DB;
USE SCHEMA SPCS;

-- Execute a shell command in the dev container
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.EXEC_COMMAND(
    command VARCHAR,
    working_dir VARCHAR DEFAULT '/home/pixi/workspace',
    timeout_seconds NUMBER DEFAULT 30
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/exec';

-- Read a file from the dev container
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.READ_FILE(
    file_path VARCHAR,
    offset NUMBER DEFAULT 0,
    max_lines NUMBER DEFAULT 2000
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/file/read';

-- Write or create a file in the dev container
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.WRITE_FILE(
    file_path VARCHAR,
    content VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/file/write';

-- Edit a file (find and replace) in the dev container
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.EDIT_FILE(
    file_path VARCHAR,
    old_string VARCHAR,
    new_string VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/file/edit';

-- List files in the dev container
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.LIST_FILES(
    directory VARCHAR DEFAULT '/home/pixi/workspace',
    pattern VARCHAR DEFAULT '*'
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/file/list';

-- Install packages via pixi, npm, pip, or conda
CREATE OR REPLACE FUNCTION DEVCONTAINER_DB.SPCS.INSTALL_PACKAGE(
    manager VARCHAR,
    packages VARCHAR
)
RETURNS VARIANT
SERVICE = DEVCONTAINER_DB.SPCS.DEVCONTAINER_SERVICE
ENDPOINT = api
AS '/api/package/install';
