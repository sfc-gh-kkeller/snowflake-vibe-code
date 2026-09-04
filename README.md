# Snowflake Vibe-Code Platform

Vibe-code full-stack applications purely through MCP — from any AI agent, from anywhere, with zero local setup. Connect Claude Desktop, CoCo, Cursor, or any MCP-compatible agent to Snowflake, and a governed cloud dev environment spins up on demand. No Docker on your laptop, no SSH keys, no VPN, no CLI installs, no port forwarding. Just authenticate with Snowflake and start building.

## Business Problem

AI coding agents need a runtime — somewhere to create files, install packages, run builds, and execute code. Today that means a local machine with Docker, Node, Python, git, and a working VPN to reach the data platform. This creates friction for every developer and a security gap for every IT team.

This platform eliminates that gap. The AI agent connects to Snowflake's managed MCP server over standard OAuth. Snowflake provisions an isolated SPCS container with a full dev environment (VS Code, terminal, package managers, Snowflake tooling) — all running inside the customer's Snowflake account, governed by their existing RBAC and network policies. The developer's machine needs nothing but a browser and their AI agent of choice.

### Key Capabilities

- **MCP-only vibe coding**: AI agents build apps entirely through MCP tool calls — no uploads, no local setup
- **Per-request role switching**: One MCP server supports all user roles. `USE ROLE` works mid-session via `OAUTH_ANY_ROLE_MODE`
- **Caller-rights + selective owner-rights**: SQL runs as the authenticated user's identity. Whitelisted procedures can escalate when needed
- **Row-level security**: Proven end-to-end — same query, different roles, different result sets
- **Full observability**: Every tool call logged to `AUDIT_LOG`. SQL queries land in `QUERY_HISTORY` with correct user/role
- **Dev mode gating**: Containers start in MCP-only mode (3 processes). VS Code, terminal, and CoCo CLI auth activate on demand and can be admin-locked per project

## Architecture

### Component Overview

```
AI Coding Agent (Claude Desktop / CoCo / Cursor)
    |
    |  Snowflake OAuth (OAUTH_ANY_ROLE_MODE = ENABLE)
    v
Snowflake Managed MCP Server (VIBE_CODE_MCP)
    |
    |  GENERIC tools -> Service Functions (UDFs)
    |  SYSTEM_EXECUTE_SQL -> native Snowflake SQL (caller RBAC)
    v
Orchestrator Service (SPCS, CPU_X64_XS)
    |  Identity-based routing, audit logging,
    |  command sanitization, project registry
    v
Per-Project Dev Containers (SPCS, CPU_X64_S, auto-scaling 1-10 nodes)

    MCP-only mode (default):
      - nginx (:8080)         -- reverse proxy, public endpoint
      - API Server (:9090)    -- file ops, shell exec, SQL via caller rights
      - Backup Daemon         -- tars workspace to stage volume every 5 min
      - Dev Server (:4000)    -- user's app (auto-detected, auto-restarted)

    Dev mode (opt-in via enable_dev_mode):
      + OpenVSCode (:3000)    -- browser IDE with Snowflake + CoCo extensions
      + Web Terminal (:7681)  -- ttyd browser terminal
      + Token Refresh         -- keeps ~/.snowflake/token fresh for CLI/extensions

Storage:
      - Block Volume (/home/pixi)     -- fast SSD, destroyed on service drop
      - Stage Volume (/backup)        -- persistent object storage, survives drops
```

### Request Flow: MCP Tool Call

```
1. Agent sends MCP tool call (e.g. project_exec "npm run dev")
        |
        v
2. Snowflake MCP Server
   - Authenticates user via OAuth session
   - Maps tool name -> Service Function (UDF)
   - Calls the UDF with batch format: {"data": [[0, arg1, arg2, ...]]}
        |
        v
3. Service Function -> Orchestrator (internal SPCS HTTP)
   - Snowflake injects Sf-Context-Current-User header with caller identity
        |
        v
4. Orchestrator API
   - Extracts caller identity from header
   - verify_access(): exact match on owner or collaborator list
   - validate_command(): checks against deny-list patterns
   - audit_log(): writes to AUDIT_LOG table
   - Proxies to project container's internal DNS
        |
        v
5. Project Container API Server (:9090)
   - Executes operation (exec, read, write, edit, list, install)
   - Returns result (stdout/stderr/exit_code, file contents, etc.)
        |
        v
6. Response flows back: container -> orchestrator -> UDF -> MCP -> agent
```

### Request Flow: SQL Execution

```
sql_exec tool (SYSTEM_EXECUTE_SQL type):
  - Runs NATIVELY in Snowflake as the authenticated user
  - Supports USE ROLE for mid-session role switching
  - Full RBAC enforcement (reads AND writes)
  - Queries appear in QUERY_HISTORY with correct USER_NAME + ROLE_NAME
  - No container involvement -- Snowflake handles it directly

elevated_exec tool (GENERIC -> owner-rights procedure):
  - Calls whitelisted stored procedures with owner privileges
  - Every invocation logged to AUDIT_LOG before execution
  - Unwhitelisted procedures are rejected
```

### Request Flow: Browser IDE (Dev Mode)

```
1. User opens https://{project-id}.snowflakecomputing.app/cocoauth
        |
        v
2. SPCS Ingress injects Sf-Context-Current-User-Token (caller JWT, ~2 min lifetime)
        |
        v
3. nginx -> API Server captures token to /tmp/caller_tokens/{USER}.json
   Browser JS refreshes every 25s to keep token alive
        |
        v
4. Token Refresh Daemon
   - Scans /tmp/caller_tokens/ for freshest valid token
   - Combines: service_token + "." + caller_token
   - Derives target account from service token JWT issuer
   - Verifies combined token opens a real Snowflake session
   - Writes ~/.snowflake/token + connections.toml
        |
        v
5. CoCo CLI / snow CLI / VS Code Extension authenticate as the user
```

### nginx Routing (port 8080 -- the only public port)

```
/api/health             -> API Server (:9090)   -- health check
/api/exec               -> API Server (:9090)   -- shell execution
/api/file/*             -> API Server (:9090)   -- file read/write/edit/list
/api/package/install    -> API Server (:9090)   -- package installation
/api/query              -> API Server (:9090)   -- SQL via caller rights
/api/cocoauth/*         -> API Server (:9090)   -- token capture/status
/cocoauth               -> API Server (:9090)   -- auth page HTML
/vscode/                -> OpenVSCode (:3000)   -- browser IDE (dev mode only)
/terminal/              -> ttyd (:7681)          -- browser terminal (dev mode only)
/*                      -> Dev Server (:4000)    -- user's app (catch-all)
```

App routes under `/api/*` that don't match the orchestrator's explicit endpoints (above) pass through to the dev server. No per-app nginx patching needed.

## MCP Tools

### Project Lifecycle

| Tool | Description |
|------|-------------|
| `create_project` | Provision a new dev container in MCP-only mode. Returns `project_id`. Takes 60-90s. |
| `list_my_projects` | List all projects owned by or shared with the caller. |
| `enable_dev_mode` | Activate VS Code, terminal, and CoCo CLI auth. Restarts the container (~60s). Admin-gatable per project. |
| `disable_dev_mode` | Return to MCP-only mode. Restarts the container. |

### Development

| Tool | Description |
|------|-------------|
| `project_exec` | Run a shell command in the container (bash -c). Default timeout 30s, max 300s. |
| `project_read` | Read a file with line numbers. Supports offset/limit for large files. |
| `project_write` | Create or overwrite a file. Max 1MB. |
| `project_edit` | Find-and-replace in a file. `old_string` must be unique. |
| `project_list` | List directory contents with glob patterns. |
| `project_install` | Install packages via npm, pip, or pixi. |

### Data & SQL

| Tool | Type | Identity |
|------|------|----------|
| `sql_exec` | `SYSTEM_EXECUTE_SQL` | **Caller** -- runs as the authenticated user with their RBAC. Supports `USE ROLE` for role switching, reads and writes. |
| `elevated_exec` | `GENERIC` (owner-rights) | **Owner** -- calls whitelisted stored procedures with elevated privileges. Audited. |

### Identity & Role Switching

The `sql_exec` tool supports per-request role switching within a single MCP session:

```sql
-- Switch to analyst role (sees only US data via row-level security)
USE ROLE ANALYST_US;
SELECT * FROM ORDERS;  -- returns only US rows

-- Switch to EU analyst (different RLS policy)
USE ROLE ANALYST_EU;
SELECT * FROM ORDERS;  -- returns only EU rows

-- Switch to writer role
USE ROLE DATA_WRITER;
INSERT INTO ORDERS VALUES (...);  -- succeeds

-- Back to analyst -- write is denied
USE ROLE ANALYST_US;
INSERT INTO ORDERS VALUES (...);  -- ERROR: Insufficient privileges
```

This works because:
- `OAUTH_ANY_ROLE_MODE = ENABLE` on the security integration
- `OAUTH_USE_SECONDARY_ROLES = NONE` (prevents privilege leakage)
- `BLOCKED_ROLES_LIST = ('ACCOUNTADMIN', 'ORGADMIN', 'SECURITYADMIN')`

## Security Model

### Identity & Access

- **Snowflake OAuth end-to-end**: MCP server authenticates users via Snowflake OAuth. The orchestrator extracts caller identity from `Sf-Context-Current-User` headers injected by SPCS.
- **Exact username matching**: Access checks use exact case-insensitive comparison. No fuzzy matching that could cause false-positive access grants.
- **Per-user project isolation**: Each project is an independent SPCS service. Users can only access projects they own or are invited to as collaborators.
- **Per-user token storage**: Caller-rights tokens stored per-user at `/tmp/caller_tokens/{USER}.json`. Collaborators don't overwrite each other's identity.
- **Admin-gated dev mode**: Admins can set `dev_mode_allowed = false` per project. Users cannot override this.

### Container Security

- **Command deny-list**: Blocks pipe-to-shell, reverse shells, rm -rf /, credential access, mount/chroot escapes.
- **File size limits**: Writes capped at 1MB.
- **Per-user quotas**: Maximum 5 active projects per user.
- **Audit logging**: Every operation logged to `AUDIT_LOG` with username, project, operation, details, timestamp, result.
- **Admin endpoint protection**: Audit log access and image updates require `ADMIN_USERS` membership.
- **No public API server**: Port 9090 is only reachable internally. nginx on port 8080 routes only known orchestrator endpoints to the API server.

### Token Management

- **Service OAuth token**: Provided by SPCS at `/snowflake/session/token`, authenticates against the hosting account.
- **Caller-rights token**: Captured from browser sessions via `/cocoauth`, combined with service token (`service.caller` format).
- **JWT-derived account targeting**: The hosting account is derived from the service token's JWT issuer claim, not environment variables.
- **Token verification**: Candidate tokens are verified by opening a real Snowflake session before being written to disk.

### Dev Server Auto-Restart

The dev server (`start.sh`, `npm run dev`, `python app.py`) is managed by supervisord with `autorestart=true`. It survives:
- Container restarts and pool suspend/resume
- Process crashes
- Manual kills (SIGTERM or SIGKILL)

If no app exists yet, the wrapper retries every 30s until the AI agent scaffolds one.

## Deployment

### Prerequisites (admin, one-time setup)

- Snowflake account with SPCS enabled
- Docker (for building images)
- Snowflake CLI (`snow`) -- only for initial deployment; end users never need it

### 1. Deploy Infrastructure

```bash
snow sql -f sql/orchestrator_setup.sql --connection <your_connection>
```

This single SQL file creates:
- Database (`DEVCONTAINER_DB`) with schemas (`SPCS`, `PROJECTS`, `DEMO`)
- Compute pools (`ORCHESTRATOR_POOL`, `PROJECT_POOL`)
- Warehouse (`DEVCONTAINER_WH`)
- Network rules and external access integration
- Registry and audit log tables
- Orchestrator service with all service functions
- MCP server with all tools
- OAuth security integration
- RBAC roles and grants
- RLS demo (optional)

### 2. Build and Push Images

```bash
snow spcs image-registry login --connection <your_connection>

# Orchestrator (lightweight, builds in seconds)
docker build --platform linux/amd64 -t orchestrator:latest -f orchestrator/Dockerfile orchestrator/

# Project base (full dev environment, builds in ~10 min)
docker build --platform linux/amd64 -t project-base:latest -f project_template/Dockerfile project_template/

# Tag and push
REPO="<org>-<account>.registry.snowflakecomputing.com/devcontainer_db/spcs/images"
docker tag orchestrator:latest $REPO/orchestrator:latest && docker push $REPO/orchestrator:latest
docker tag project-base:latest $REPO/project-base:latest && docker push $REPO/project-base:latest
```

### 3. Connect an AI Agent

In Claude Desktop, CoCo Desktop, or any MCP-compatible client:

1. Add the MCP server URL:
   ```
   https://<org>-<account>.snowflakecomputing.com/api/v2/databases/DEVCONTAINER_DB/schemas/SPCS/mcp-servers/VIBE_CODE_MCP
   ```

2. Get OAuth credentials:
   ```sql
   SELECT SYSTEM$SHOW_OAUTH_CLIENT_SECRETS('<your_oauth_integration_name>');
   ```

3. Authenticate via the OAuth flow and start coding:
   > "Create a new Next.js project called my-dashboard"

### 4. Optional: Enable Dev Mode

After creating a project, enable the browser IDE:
> "Enable dev mode for my project"

Then open `https://{project-url}/cocoauth` in your browser to activate CoCo CLI and VS Code extension auth inside the container. Access VS Code at `/vscode/` and terminal at `/terminal/`.

## Project Structure

```
.
├── orchestrator/
│   ├── Dockerfile              # Lightweight Python image with snowflake-connector
│   └── api.py                  # Orchestrator API: routing, auth, registry, audit,
│                               #   dev mode toggle, admin controls
├── project_template/
│   ├── Dockerfile              # Full dev environment (Debian + nginx + VSCode + tools)
│   ├── server.py               # Container API server (file ops, exec, SQL query)
│   ├── backup.py               # Workspace backup daemon (tar to stage volume)
│   ├── token_refresh.py        # Keeps ~/.snowflake/token + connections.toml fresh
│   ├── nginx.conf              # Reverse proxy (orchestrator endpoints, VSCode,
│   │                           #   terminal, dev server catch-all)
│   ├── supervisord.conf        # Base process manager (nginx, api-server, backup, devserver)
│   ├── supervisord-devmode.conf # Dev mode processes (VSCode, terminal, token-refresh)
│   ├── devserver-wrapper.sh    # Auto-detects and starts the user's app on :4000
│   └── entrypoint.sh           # First-boot init, dev mode gating
├── sql/
│   └── orchestrator_setup.sql  # Complete infrastructure DDL (single file)
└── README.md
```

## Snowflake Objects Created

### Schemas
- `DEVCONTAINER_DB.SPCS` -- orchestrator, service functions, MCP server
- `DEVCONTAINER_DB.PROJECTS` -- per-project services, registry, audit log, backups
- `DEVCONTAINER_DB.DEMO` -- RLS demo data (optional)

### Compute
- `ORCHESTRATOR_POOL` -- CPU_X64_XS, 1 node, auto-suspend 1h
- `PROJECT_POOL` -- CPU_X64_S, 1-10 nodes, auto-suspend 15m

### Services
- `ORCHESTRATOR_SERVICE` -- always-on orchestrator
- `PROJ_{ID}` -- per-project dev containers (created on demand)

### Tables
- `PROJECTS.REGISTRY` -- project metadata, owner, collaborators, dev_mode_allowed
- `PROJECTS.AUDIT_LOG` -- every operation with username, timestamp, result

### MCP Server
- `SPCS.VIBE_CODE_MCP` -- 12 tools exposed to AI agents

### Key Procedures
- `CREATE_PROJECT_PROC` -- provisions a new SPCS service with block + stage volumes
- `ENABLE_DEV_MODE` / `DISABLE_DEV_MODE` -- toggles browser IDE and CLI auth
- `ELEVATED_EXEC` -- whitelisted owner-rights procedure execution
- `PROJECT_EXEC/READ/WRITE/EDIT/LIST/INSTALL` -- container operations via orchestrator

## Cost Management

- **Compute pool auto-suspend**: `PROJECT_POOL` suspends after 15 min idle, `ORCHESTRATOR_POOL` after 1 hour
- **Auto-resume**: Pools resume automatically when a tool call arrives
- **MCP-only default**: Containers start with 4 processes instead of 7, reducing resource usage
- **Project destroy**: Drops the service, cleans up stage backups, removes registry entry

## Known Limitations

- MCP tool responses are capped at ~250KB (large outputs are truncated)
- Cold start after pool auto-suspend takes 30-90 seconds
- No streaming -- long-running commands should write output to a file and be read separately
- Container-to-container networking within the same account is not isolated by SPCS (known platform limitation)
- `enable_dev_mode` / `disable_dev_mode` restart the container (~60s downtime)
- One active CLI/extension identity per container at a time (the freshest caller token wins)

## License

Apache 2.0
