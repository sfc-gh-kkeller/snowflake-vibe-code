-- =============================================================================
-- Agent-to-Agent Vibe-Code: Cortex Coding Agent + Snowflake App Runtime
-- Zero infrastructure. No containers, no orchestrator, no Docker.
-- =============================================================================

USE ROLE ACCOUNTADMIN;

-- 1. Skills stage (stores the app-builder skill)
CREATE STAGE IF NOT EXISTS DEVCONTAINER_DB.SPCS.SKILLS DIRECTORY = (ENABLE = TRUE);

-- Upload skill: PUT file://agent-2-agent/skill/SKILL.md @DEVCONTAINER_DB.SPCS.SKILLS/app-builder/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE

-- 2. Cortex Coding Agent
-- NOTE: code_toolset_all in an agent object doesn't activate sandbox tools when called via AGENT_RUN SQL.
-- The BUILD_APP procedure uses inline config instead. This agent object is for reference/Snowsight UI.
CREATE OR REPLACE AGENT DEVCONTAINER_DB.SPCS.VIBE_CODE_AGENT
  COMMENT = 'Vibe-code web apps and deploy to Snowflake App Runtime'
  FROM SPECIFICATION
  $$
  models:
    orchestration: auto
  instructions:
    system: "You are a full-stack web app builder. You create Next.js applications on Snowflake App Runtime. Use the app-builder skill for project structure and deployment. Always scaffold apps in /workspace/apps/{app-name}/. After building, deploy with snow app deploy and return the public URL."
    orchestration: "Use the coding tools for all file creation, editing, and bash commands. Use SQL for Snowflake data access and schema exploration."
  tools:
    - tool_spec:
        type: code_toolset_all
        name: code_toolset_all
  tool_resources:
    code_toolset_all:
      permission_policy:
        type: always_allow
      workspace_mounts:
        - name: "USER$.PUBLIC.DEFAULT$"
          type: workspace
          mount_path: /workspace
      artifact_repositories:
        - SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY
  skills:
    - name: app-builder
      source:
        type: STAGE
        path: "@DEVCONTAINER_DB.SPCS.SKILLS/app-builder"
  $$;

-- 3. BUILD_APP procedure (wraps AGENT_RUN with inline config to ensure sandbox tools activate)
CREATE OR REPLACE PROCEDURE DEVCONTAINER_DB.SPCS.BUILD_APP(DESCRIPTION VARCHAR)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'build_app'
EXECUTE AS CALLER
AS '
import json

def build_app(session, description):
    if not description or not description.strip():
        return json.dumps({"error": "Please describe the app you want to build"})

    request_body = json.dumps({
        "models": {"orchestration": "auto"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": description
                    }
                ]
            }
        ],
        "tools": [
            {"tool_spec": {"type": "code_toolset_all", "name": "code_toolset_all"}}
        ],
        "tool_resources": {
            "code_toolset_all": {
                "permission_policy": {"type": "always_allow"},
                "workspace_mounts": [
                    {"name": "USER$.PUBLIC.DEFAULT$", "type": "workspace", "mount_path": "/workspace"}
                ],
                "artifact_repositories": ["SNOWFLAKE.SNOWPARK.PYPI_SHARED_REPOSITORY"]
            }
        },
        "instructions": {
            "system": "You are a full-stack web app builder. You create Next.js applications on Snowflake App Runtime. Always scaffold apps in /workspace/apps/{app-name}/. Use bash for npm install and snow app deploy. Return the public URL when done."
        },
        "skills": [
            {
                "name": "app-builder",
                "source": {
                    "type": "STAGE",
                    "path": "@DEVCONTAINER_DB.SPCS.SKILLS/app-builder"
                }
            }
        ]
    })

    dd = chr(36) + chr(36)
    sql = f"SELECT SNOWFLAKE.CORTEX.AGENT_RUN({dd}{request_body}{dd}, TRUE)"

    try:
        result = session.sql(sql).collect()
        if result and result[0][0]:
            resp = json.loads(str(result[0][0]))
            content = resp.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            thread_id = resp.get("metadata", {}).get("thread_id")
            return json.dumps({
                "response": " ".join(texts),
                "thread_id": thread_id,
                "status": resp.get("metadata", {}).get("status", "unknown")
            })
        return json.dumps({"error": "No response from agent"})
    except Exception as e:
        return json.dumps({"error": str(e)})
';

-- 4. V2 MCP Server (agent-to-agent interface)
CREATE OR REPLACE MCP SERVER DEVCONTAINER_DB.SPCS.VIBE_CODE_MCP_V2
FROM SPECIFICATION $$
tools:
  - title: "Build App"
    name: "build_app"
    type: "GENERIC"
    identifier: "DEVCONTAINER_DB.SPCS.BUILD_APP"
    description: "Describe a web app and have it built and deployed to Snowflake App Runtime. The Cortex Coding Agent scaffolds a Next.js project, installs dependencies, and deploys to a live URL. This may take several minutes."
    config:
      type: "procedure"
      warehouse: "DEVCONTAINER_WH"
      input_schema:
        type: "object"
        properties:
          description:
            type: "string"
            description: "Describe the app you want built. Include what data to show, what interactions to support, and any specific design preferences."

  - title: "Execute SQL"
    name: "sql_exec"
    type: "SYSTEM_EXECUTE_SQL"
    description: "Run SQL as YOUR identity with YOUR Snowflake RBAC. Supports USE ROLE for role switching, reads and writes."
    config:
      read_only: false
      query_timeout: 600
      warehouse: "DEVCONTAINER_WH"
$$;

-- 5. Grants
GRANT USAGE ON MCP SERVER DEVCONTAINER_DB.SPCS.VIBE_CODE_MCP_V2 TO ROLE ACCOUNTADMIN;
