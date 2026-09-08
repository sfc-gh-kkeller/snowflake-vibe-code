# Agent-to-Agent Vibe-Code (Cortex Agent + SAR)

An alternative to the SPCS container approach. Uses Snowflake's native Cortex Coding Agent as the vibe-coding runtime and Snowflake App Runtime (SAR) for app hosting. Zero infrastructure to manage.

## How it works

```
External AI Agent (Claude Desktop / Cursor / any MCP client)
    |
    |  MCP (Snowflake OAuth)
    v
MCP Server (VIBE_CODE_MCP_V2)
    |
    |  build_app tool -> BUILD_APP procedure
    v
AGENT_RUN (Snowflake Cortex)
    |
    |  code_toolset_all: bash, read, write, edit, SQL, web_search
    |  workspace: stage-backed persistent files
    |  skills: app-builder skill
    v
Coding Agent sandbox
    |
    |  Scaffolds Next.js project
    |  Installs dependencies (npm install)
    |  Deploys via snow app deploy
    v
Snowflake App Runtime (SAR)
    |
    |  Public URL: *.snowflakecomputing.app
    |  SSO + RBAC inherited from Snowflake
    v
End Users (browser)
```

## What this eliminates vs. the SPCS approach

- No orchestrator service
- No custom API server, nginx, supervisord, token refresh, backup daemon
- No Docker image builds or pushes
- No compute pool management
- No custom MCP server with 12 tools

The entire runtime is Snowflake-managed. The "infrastructure" is three SQL objects: an agent, a procedure, and an MCP server.

## Setup

```bash
# 1. Upload the app-builder skill
snow sql -q "PUT file://agent-2-agent/skill/SKILL.md @DEVCONTAINER_DB.SPCS.SKILLS/app-builder/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE" --connection <your_connection>

# 2. Create agent, procedure, and MCP server
snow sql -f agent-2-agent/setup.sql --connection <your_connection>
```

## Usage

### From any MCP client

Connect to:
```
https://<org>-<account>.snowflakecomputing.com/api/v2/databases/DEVCONTAINER_DB/schemas/SPCS/mcp-servers/VIBE_CODE_MCP_V2
```

Then: "Build me a dashboard that shows warehouse credit usage"

### From SQL

```sql
CALL DEVCONTAINER_DB.SPCS.BUILD_APP('Build a dashboard that shows warehouse credit usage');
```

### Direct agent call

```sql
SELECT SNOWFLAKE.CORTEX.AGENT_RUN($${
  "models": {"orchestration": "auto"},
  "messages": [{"role": "user", "content": [{"type": "text", "text": "Build a dashboard..."}]}],
  "tools": [{"tool_spec": {"type": "code_toolset_all", "name": "code_toolset_all"}}],
  "tool_resources": {
    "code_toolset_all": {
      "permission_policy": {"type": "always_allow"},
      "workspace_mounts": [{"name": "USER$.PUBLIC.DEFAULT$", "type": "workspace", "mount_path": "/workspace"}]
    }
  }
}$$, TRUE);
```

## Status: PARKED

The agent-to-agent approach works for building apps (file creation, npm install, bash — all verified in the Coding Agent sandbox). The blocker is deploying to Snowflake App Runtime (SAR):

| Component | Snow CLI version | SAR support |
|-----------|-----------------|-------------|
| Latest release | **3.26.0** | Yes (`app.yml` v2 GA) |
| Coding Agent sandbox | **3.24.1** | No (pip locked, can't upgrade) |
| SPCS deployer service | 3.26.0 | Fails — service identity has no personal database |

SAR builds run inside the calling user's personal database (`USER$<username>`). Service identities don't have personal databases, so neither an SPCS deployer service nor the sandbox (which runs as a service) can complete the build.

**When the sandbox gets snow CLI 3.25+**, this approach works end-to-end: agent builds files → `snow app deploy` from bash → live SAR URL. No SPCS containers needed.

**Alternative (works today)**: Use the V1 SPCS approach — AI agent calls `create_project` → `project_write` → `project_exec` to build and serve apps in SPCS containers. See the root README.

## Key learnings

1. **Agent object vs inline config**: `code_toolset_all` sandbox tools only activate when using inline config in `AGENT_RUN`, not when referencing an agent object. The BUILD_APP procedure uses inline config for this reason.

2. **Workspace persistence**: Files written to `/workspace/` persist across agent calls because the workspace is backed by a Snowflake stage (`USER$.PUBLIC.DEFAULT$`).

3. **Permission policy**: Set to `always_allow` for unattended execution. The default `always_ask` pauses at every state-modifying tool call, which doesn't work in a single-response SQL function.

4. **SAR deployment blocker**: The sandbox has snow CLI 3.24.1; SAR (`app.yml` v2) requires 3.25+. pip is locked by Bazel rules in the sandbox so you can't upgrade. A separate SPCS deployer service was tested but SAR builds require a human user identity (personal database), which service identities don't have.

## Comparison: SPCS vs Agent-to-Agent

| Aspect | SPCS Containers | Agent-to-Agent |
|--------|----------------|----------------|
| Infrastructure | Compute pools, Docker images, orchestrator | Zero — 3 SQL objects |
| Runtime | Full Linux container (Debian) | Managed sandbox (Python/bash) |
| Dev tools | VS Code, terminal, CoCo CLI | None — MCP only |
| App hosting | nginx inside container | Snowflake App Runtime (SAR) |
| Persistence | Block + stage volumes | Workspace stage |
| Cold start | 60-90s (container + image pull) | ~5s (sandbox provision) |
| Identity | Custom token management | Native caller-rights |
| Cost | Compute pool nodes running | Pay-per-use (Cortex credits) |
| Flexibility | Full container = anything | Sandbox limitations apply |
