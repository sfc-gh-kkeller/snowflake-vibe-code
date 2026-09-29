# Agent-to-Agent Vibe-Code (Cortex Agent + SAR)

An alternative to the SPCS container approach. Uses Snowflake's native Cortex Coding Agent as the vibe-coding runtime and Snowflake App Runtime (SAR) for app hosting. Zero infrastructure to manage.

## Status: WORKING

Tested and verified end-to-end on Sep 29, 2026. The Coding Agent sandbox now has snow CLI 3.27.0, and `snow app deploy` works from inside the sandbox.

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
    |  Scaffolds Next.js project in /tmp
    |  Runs snow app setup + snow app deploy
    |  SAR builds remotely (no local npm install)
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

## Key learnings

1. **Agent object vs inline config**: `code_toolset_all` sandbox tools only activate when using inline config in `AGENT_RUN`, not when referencing an agent object. The BUILD_APP procedure uses inline config for this reason.

2. **Workspace stage limitations**: The `/workspace` mount is backed by a Snowflake stage which does NOT support POSIX atomic renames. This means `npm install` fails with EIO errors on `/workspace`. Always scaffold projects in `/tmp` instead. SAR does a remote build, so local `npm install` is not needed.

3. **Pre-configured snow connection**: The sandbox automatically has a `default` snow CLI connection configured with OAuth token auth. No manual `snow connection add` is needed. The connection uses the calling user's identity, role, and warehouse.

4. **SAR builds in personal database**: `snow app setup` resolves to `USER$<username>` as the build database. Since the sandbox runs as the calling user (not a service identity), personal database access works correctly.

5. **Permission policy**: Set to `always_allow` for unattended execution. The default `always_ask` pauses at every state-modifying tool call, which doesn't work in a single-response SQL function.

6. **Timing**: The full cycle (agent thinking + file creation + snow app setup + snow app deploy with remote build) takes 5-10 minutes. Set `STATEMENT_TIMEOUT_IN_SECONDS` accordingly.

## Sandbox environment

| Component | Value |
|-----------|-------|
| Snow CLI | 3.27.0 |
| Connection | `default` (OAuth, auto-configured) |
| Auth | `/snowflake/session/token` |
| Build database | `USER$<username>` (personal) |
| Workspace | `/workspace` -> `USER$.PUBLIC.DEFAULT$` (stage-backed) |
| Temp dir | `/tmp` (local filesystem, use for npm/builds) |

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
