-- =============================================================================
-- SPCS DevContainer: MCP Server Configuration
-- Exposes service functions as GENERIC tools for Claude Desktop/Cowork
-- =============================================================================

USE ROLE ACCOUNTADMIN;
USE DATABASE DEVCONTAINER_DB;
USE SCHEMA SPCS;

CREATE OR REPLACE MCP SERVER DEVCONTAINER_DB.SPCS.DEV_MCP_SERVER
FROM SPECIFICATION $$
tools:
  - title: "Execute Shell Command"
    name: "exec_command"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.EXEC_COMMAND"
    description: "Execute a shell command in the dev container. Returns stdout, stderr, and exit code. Use for running builds, tests, git operations, starting dev servers, and any CLI tool. The container has pixi, npm, python, git, and standard unix tools available."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          command:
            description: "The shell command to execute (runs in bash -lc)"
            type: "string"
          working_dir:
            description: "Working directory for the command (default: /home/pixi/workspace)"
            type: "string"
          timeout_seconds:
            description: "Max execution time in seconds (default: 30, max: 300)"
            type: "number"

  - title: "Read File"
    name: "read_file"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.READ_FILE"
    description: "Read the contents of a file in the dev container. Returns numbered lines. Use offset and max_lines for large files."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            description: "Absolute path to the file to read"
            type: "string"
          offset:
            description: "Line number to start from, 0-indexed (default: 0)"
            type: "number"
          max_lines:
            description: "Maximum number of lines to return (default: 2000)"
            type: "number"

  - title: "Write File"
    name: "write_file"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.WRITE_FILE"
    description: "Create or overwrite a file in the dev container with the given content. Parent directories are created automatically."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            description: "Absolute path for the file to write"
            type: "string"
          content:
            description: "The full file content to write"
            type: "string"

  - title: "Edit File"
    name: "edit_file"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.EDIT_FILE"
    description: "Find and replace text in a file. The old_string must match exactly one occurrence in the file. Provide enough context to make the match unique."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          file_path:
            description: "Absolute path to the file to edit"
            type: "string"
          old_string:
            description: "Exact text to find (must be unique in the file)"
            type: "string"
          new_string:
            description: "Replacement text"
            type: "string"

  - title: "List Files"
    name: "list_files"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.LIST_FILES"
    description: "List files and directories in the dev container. Supports glob patterns like '**/*.py' or '*.ts'."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          directory:
            description: "Directory to list (default: /home/pixi/workspace)"
            type: "string"
          pattern:
            description: "Glob pattern to filter results (default: *)"
            type: "string"

  - title: "Install Package"
    name: "install_package"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.INSTALL_PACKAGE"
    description: "Install packages in the dev container. Supports pixi (conda-forge, recommended), npm, and pip. Installed packages persist across container restarts."
    config:
      type: "function"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          manager:
            description: "Package manager to use: pixi, npm, pip, or conda"
            type: "string"
          packages:
            description: "Space-separated list of packages to install"
            type: "string"

  - title: "Execute SQL"
    name: "sql_exec"
    type: "SYSTEM_EXECUTE_SQL"
    description: "Execute SQL queries against Snowflake as the authenticated user. Use for querying data, creating objects, and managing Snowflake resources."
    config:
      read_only: false
      query_timeout: 600
      warehouse: "DEVCONTAINER_WH"
$$;
