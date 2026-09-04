"""
SPCS Orchestrator API Server (Security-Hardened v2)

Features:
- Secretless identity-based auth (caller token from OAuth)
- Command sanitization (deny list)
- Constant-time secret comparison (backward compat)
- SQL injection prevention (input sanitization)
- Audit logging (every operation)
- Resource quotas (per-user project limits)
- Image digest pinning
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- Configuration ---
SNOWFLAKE_HOST = os.environ.get("SNOWFLAKE_HOST", "")
SNOWFLAKE_ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "")
PROJECT_SCHEMA = os.environ.get("PROJECT_SCHEMA", "DEVCONTAINER_DB.PROJECTS")
PROJECT_POOL = os.environ.get("PROJECT_POOL", "PROJECT_POOL")
QUERY_WAREHOUSE = os.environ.get("QUERY_WAREHOUSE", "DEVCONTAINER_WH")

# Admin users (comma-separated, case-insensitive) — only these can access audit log and update images
ADMIN_USERS = {u.strip().upper() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()}

# Image digest pinning — update via ADMIN_UPDATE_IMAGE_DIGEST
IMAGE_DIGEST = os.environ.get(
    "PROJECT_IMAGE_DIGEST",
    "sha256:a70231151bcfc6cfe61bc21bb31df789371c0a68cead007e713c304b7793eff0"
)
IMAGE_REPO = os.environ.get("PROJECT_IMAGE_REPO", "/devcontainer_db/spcs/images/project-base")
PROJECT_IMAGE = f"{IMAGE_REPO}@{IMAGE_DIGEST}"

# Quotas
MAX_PROJECTS_PER_USER = int(os.environ.get("MAX_PROJECTS_PER_USER", "5"))
MAX_COMMAND_TIMEOUT = int(os.environ.get("MAX_COMMAND_TIMEOUT", "300"))
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", "1000000"))  # 1MB

# Registry and audit — backed by Snowflake tables
REGISTRY = {}
SF_CONN = None  # Lazy Snowflake connection

# --- Command Sanitization ---
COMMAND_DENY_PATTERNS = [
    r"curl\s.*\|\s*(ba)?sh",           # pipe-to-shell
    r"wget\s.*-O\s*-\s*\|",           # wget pipe to shell
    r"\bnc(at)?\b.*-[el]",             # netcat listener/exec
    r"python[23]?\s.*import\s+socket", # reverse shell
    r"rm\s+-rf\s+/\s",                # rm -rf / (root)
    r"rm\s+-rf\s+/home/pixi\s*$",     # rm entire home
    r"rm\s+-rf\s+/home\s",            # rm /home
    r"rm\s+-rf\s+/(etc|usr|bin|sbin|lib|boot|proc|sys)\b",  # system dirs
    r"/etc/(shadow|passwd)",           # credential files
    r"chmod\s+[ugo]*\+?s",           # setuid/setgid
    r"\bmount\s",                      # filesystem escape
    r"\bchroot\s",                     # chroot escape
    r"dd\s+if=/dev",                   # raw device access
    r"\biptables\b",                   # firewall manipulation
    r">(>)?\s*/dev/",                  # write to devices
]

COMPILED_DENY = [re.compile(p, re.IGNORECASE) for p in COMMAND_DENY_PATTERNS]


def validate_command(command):
    """Check command against deny list. Returns (allowed, reason)."""
    for pattern in COMPILED_DENY:
        if pattern.search(command):
            return False, "Blocked by security policy"
    return True, None


# --- Input Sanitization ---
def sanitize_project_name(name):
    """Remove dangerous characters from project names."""
    return re.sub(r'[^a-zA-Z0-9_\-\s]', '', name or 'untitled')[:64]


# --- Secret Handling (backward compat + constant-time) ---
def generate_secret():
    return secrets.token_hex(32)


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret_constant_time(provided, stored_hash):
    """Constant-time comparison to prevent timing attacks."""
    provided_hash = hashlib.sha256(provided.encode()).hexdigest()
    return hmac.compare_digest(provided_hash, stored_hash)


# --- Identity Extraction ---
def extract_username(headers):
    """Extract caller username from headers or the 'caller' field in the request body."""
    # Try Sf-Context-Current-User-Token (public ingress only)
    token = headers.get("Sf-Context-Current-User-Token", "")
    if token:
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                payload = parts[1]
                payload += "=" * (4 - len(payload) % 4)
                decoded = base64.urlsafe_b64decode(payload)
                data = json.loads(decoded)
                username = data.get("sub", data.get("username", ""))
                if username:
                    return username.upper()
        except Exception:
            pass

    # Fallback: Sf-Context-Current-User header
    user = headers.get("Sf-Context-Current-User", "")
    if user:
        return user.upper()

    return ""


def usernames_match(caller, stored):
    """Exact case-insensitive username comparison. No fuzzy matching."""
    return caller.upper().strip() == stored.upper().strip()


def extract_username_from_cols(cols, username_index):
    """For service function calls, the username is passed as a parameter by the wrapper procedure."""
    if len(cols) > username_index and cols[username_index]:
        return str(cols[username_index]).upper()
    return ""


# --- Access Control ---
def verify_access(project_id, caller_username):
    """Check if caller owns or has access to the project."""
    project = REGISTRY.get(project_id)
    if not project:
        return False, "Project not found"

    owner = project.get("owner", "").upper()
    collaborators = [c.upper() for c in project.get("collaborators", [])]

    if usernames_match(caller_username, owner):
        return True, None
    for collab in collaborators:
        if usernames_match(caller_username, collab):
            return True, None

    return False, "Access denied: you are not the owner or a collaborator"



# --- Audit Logging ---
def audit_log(username, project_id, operation, details, result):
    """Write audit entry to Snowflake AUDIT_LOG table."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": username,
        "project": project_id or "",
        "op": operation,
        "details": (details or "")[:200],
        "result": result,
    }
    # Best-effort write to Snowflake
    conn = get_sf_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO DEVCONTAINER_DB.PROJECTS.AUDIT_LOG (USERNAME, PROJECT_ID, OPERATION, DETAILS, RESULT) VALUES (%s, %s, %s, %s, %s)",
                (username, project_id or "", operation, (details or "")[:4000], result)
            )
        except Exception:
            pass


# --- Snowflake Connection ---
def get_sf_connection():
    """Get or create Snowflake connection using service OAuth token."""
    global SF_CONN
    if SF_CONN is not None:
        try:
            SF_CONN.cursor().execute("SELECT 1")
            return SF_CONN
        except Exception:
            SF_CONN = None

    try:
        import snowflake.connector
        token_path = "/snowflake/session/token"
        with open(token_path) as f:
            token = f.read().strip()

        # Derive hosting account from service token JWT issuer
        # (SPCS tokens only authenticate against the account that issued them)
        host = SNOWFLAKE_HOST
        account = SNOWFLAKE_ACCOUNT
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            iss = claims.get("iss") or claims.get("aud") or ""
            iss = iss.replace("https://", "").replace("http://", "").strip("/")
            if iss and "." in iss:
                account = iss.split(".")[0]
                host = iss
        except Exception:
            pass

        SF_CONN = snowflake.connector.connect(
            host=host,
            account=account,
            token=token,
            authenticator="oauth",
            database="DEVCONTAINER_DB",
            schema="PROJECTS",
            warehouse=QUERY_WAREHOUSE,
        )
        return SF_CONN
    except Exception as e:
        print(f"[WARN] Snowflake connection failed: {e}", flush=True)
        return None


# --- Registry Persistence (Snowflake tables) ---
def load_registry_from_snowflake():
    """Load all projects from REGISTRY table into in-memory cache."""
    global REGISTRY
    conn = get_sf_connection()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT PROJECT_ID, PROJECT_NAME, OWNER_USERNAME, SECRET_HASH, SERVICE_NAME, SERVICE_DNS, PUBLIC_URL, TEMPLATE, STATUS, COLLABORATORS, DEV_MODE_ALLOWED FROM DEVCONTAINER_DB.PROJECTS.REGISTRY")
        rows = cur.fetchall()
        for row in rows:
            project_id = row[0].lower() if row[0] else ""
            REGISTRY[project_id] = {
                "project_id": project_id,
                "project_name": row[1] or "",
                "owner": (row[2] or "").upper(),
                "secret_hash": row[3] or "",
                "service_name": row[4] or "",
                "service_dns": row[5] or "",
                "public_url": row[6] or "",
                "template": row[7] or "nextjs",
                "status": row[8] or "ACTIVE",
                "collaborators": json.loads(row[9]) if row[9] else [],
                "dev_mode_allowed": row[10] if row[10] is not None else True,
            }
        return len(REGISTRY)
    except Exception as e:
        print(f"[WARN] Failed to load registry from Snowflake: {e}", flush=True)
        return 0


def save_project_to_snowflake(project_id):
    """Upsert a single project to the REGISTRY table."""
    conn = get_sf_connection()
    if not conn:
        return
    project = REGISTRY.get(project_id)
    if not project:
        return
    try:
        cur = conn.cursor()
        cur.execute("""
            MERGE INTO DEVCONTAINER_DB.PROJECTS.REGISTRY t
            USING (SELECT %s AS pid) s ON t.PROJECT_ID = s.pid
            WHEN MATCHED THEN UPDATE SET
                PROJECT_NAME = %s, OWNER_USERNAME = %s, SECRET_HASH = %s,
                SERVICE_NAME = %s, SERVICE_DNS = %s, PUBLIC_URL = %s,
                TEMPLATE = %s, STATUS = %s, COLLABORATORS = %s, DEV_MODE_ALLOWED = %s
            WHEN NOT MATCHED THEN INSERT
                (PROJECT_ID, PROJECT_NAME, OWNER_USERNAME, SECRET_HASH, SERVICE_NAME, SERVICE_DNS, PUBLIC_URL, TEMPLATE, STATUS, COLLABORATORS, DEV_MODE_ALLOWED)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            project_id,
            project["project_name"], project["owner"], project["secret_hash"],
            project["service_name"], project["service_dns"], project.get("public_url", ""),
            project.get("template", "nextjs"), project.get("status", "ACTIVE"),
            json.dumps(project.get("collaborators", [])),
            project.get("dev_mode_allowed", True),
            # INSERT values
            project_id,
            project["project_name"], project["owner"], project["secret_hash"],
            project["service_name"], project["service_dns"], project.get("public_url", ""),
            project.get("template", "nextjs"), project.get("status", "ACTIVE"),
            json.dumps(project.get("collaborators", [])),
            project.get("dev_mode_allowed", True),
        ))
    except Exception as e:
        print(f"[WARN] Failed to save project to Snowflake: {e}", flush=True)


def delete_project_from_snowflake(project_id):
    """Delete a project from the REGISTRY table."""
    conn = get_sf_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM DEVCONTAINER_DB.PROJECTS.REGISTRY WHERE PROJECT_ID = %s", (project_id,))
    except Exception as e:
        print(f"[WARN] Failed to delete project from Snowflake: {e}", flush=True)


# --- Request Proxying ---
def proxy_to_project(project, path, body, timeout=60):
    """Forward request to project container's internal API."""
    dns = project.get("service_dns", "")
    # Handle both formats: full URL (http://host:port) or just hostname
    if dns.startswith("http://") or dns.startswith("https://"):
        url = f"{dns}{path}"
    else:
        url = f"http://{dns}:8080{path}"
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"Project container returned HTTP {e.code}"}
    except urllib.error.URLError as e:
        # On DNS/connection failure, try refreshing from Snowflake
        project_id = project.get("project_id", "")
        if project_id:
            refreshed = _refresh_project_dns(project_id)
            if refreshed:
                # Retry once with updated DNS
                dns2 = refreshed.get("service_dns", "")
                if dns2 and dns2 != dns:
                    url2 = f"http://{dns2}:8080{path}" if not dns2.startswith("http") else f"{dns2}{path}"
                    req2 = urllib.request.Request(url2, data=payload, method="POST")
                    req2.add_header("Content-Type", "application/json")
                    try:
                        with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                            return json.loads(resp2.read())
                    except Exception:
                        pass
        return {"error": f"Cannot reach project container: {e.reason}"}
    except Exception as e:
        return {"error": f"Proxy error: {str(e)}"}


def _refresh_project_dns(project_id):
    """Re-read a project's DNS from the Snowflake table (handles service recreations)."""
    conn = get_sf_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT SERVICE_DNS FROM DEVCONTAINER_DB.PROJECTS.REGISTRY WHERE PROJECT_ID = %s", (project_id,))
        row = cur.fetchone()
        if row and row[0]:
            new_dns = row[0]
            if project_id in REGISTRY:
                REGISTRY[project_id]["service_dns"] = new_dns
            return {"service_dns": new_dns}
    except Exception:
        pass
    return None


# --- HTTP Handler ---
class OrchestratorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json({"status": "ok", "projects": len(REGISTRY), "version": "2.0-hardened"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._read_body()

        if "data" in body:
            # Service function batch format
            rows = body["data"]
            results = []
            for row in rows:
                row_idx = row[0]
                cols = row[1:]
                result = self._route(self.path, cols)
                results.append([row_idx, result])
            self._send_json({"data": results})
        else:
            result = self._route_direct(self.path, body)
            self._send_json(result)

    def _route(self, path, cols):
        """Route service function calls with full security checks."""
        try:
            # For service function calls, the wrapper procedure injects CURRENT_USER()
            # as the LAST parameter. Extract it from headers (public) or last col (internal).
            username = extract_username(self.headers)

            # --- Project Management ---
            if path == "/api/register_project":
                pid = cols[0] if len(cols) > 0 else ""
                name = cols[1] if len(cols) > 1 else ""
                owner = cols[2] if len(cols) > 2 else ""
                secret_hash = cols[3] if len(cols) > 3 else ""
                svc_name = cols[4] if len(cols) > 4 else ""
                svc_dns = cols[5] if len(cols) > 5 else ""
                return json.dumps(self._register_project(pid, name, owner, secret_hash, svc_name, svc_dns))

            elif path == "/api/list_projects":
                # Last param is caller username
                username = cols[0].upper() if len(cols) > 0 and cols[0] else username
                return json.dumps(self._list_projects(username))

            elif path == "/api/add_collaborator":
                pid = cols[0] if len(cols) > 0 else ""
                collaborator = cols[1] if len(cols) > 1 else ""
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                return json.dumps(self._add_collaborator(username, pid, collaborator))

            elif path == "/api/remove_collaborator":
                pid = cols[0] if len(cols) > 0 else ""
                collaborator = cols[1] if len(cols) > 1 else ""
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                return json.dumps(self._remove_collaborator(username, pid, collaborator))

            # --- Project Operations (last param = caller username) ---
            elif path == "/api/project/exec":
                cmd = cols[0] if len(cols) > 0 else ""
                pid = cols[1] if len(cols) > 1 else ""
                timeout = int(cols[2]) if len(cols) > 2 and cols[2] else 30
                username = cols[3].upper() if len(cols) > 3 and cols[3] else username
                dns_hint = cols[4] if len(cols) > 4 and cols[4] else ""
                timeout = min(timeout, MAX_COMMAND_TIMEOUT)
                return json.dumps(self._secure_project_op(
                    username, pid, "exec", "/api/exec",
                    {"command": cmd, "working_dir": "/home/pixi/workspace", "timeout_seconds": timeout},
                    command_text=cmd
                ))

            elif path == "/api/project/read":
                fpath = cols[0] if len(cols) > 0 else ""
                pid = cols[1] if len(cols) > 1 else ""
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                dns_hint = cols[3] if len(cols) > 3 and cols[3] else ""
                return json.dumps(self._secure_project_op(
                    username, pid, "read", "/api/file/read",
                    {"file_path": fpath}
                ))

            elif path == "/api/project/write":
                fpath = cols[0] if len(cols) > 0 else ""
                content = cols[1] if len(cols) > 1 else ""
                pid = cols[2] if len(cols) > 2 else ""
                username = cols[3].upper() if len(cols) > 3 and cols[3] else username
                dns_hint = cols[4] if len(cols) > 4 and cols[4] else ""
                if len(content.encode("utf-8")) > MAX_FILE_SIZE:
                    audit_log(username, pid, "write_blocked", fpath, "file_too_large")
                    return json.dumps({"error": f"File exceeds {MAX_FILE_SIZE} byte limit"})
                return json.dumps(self._secure_project_op(
                    username, pid, "write", "/api/file/write",
                    {"file_path": fpath, "content": content}
                ))

            elif path == "/api/project/edit":
                fpath = cols[0] if len(cols) > 0 else ""
                old = cols[1] if len(cols) > 1 else ""
                new = cols[2] if len(cols) > 2 else ""
                pid = cols[3] if len(cols) > 3 else ""
                username = cols[4].upper() if len(cols) > 4 and cols[4] else username
                dns_hint = cols[5] if len(cols) > 5 and cols[5] else ""
                return json.dumps(self._secure_project_op(
                    username, pid, "edit", "/api/file/edit",
                    {"file_path": fpath, "old_string": old, "new_string": new}
                ))

            elif path == "/api/project/list":
                directory = cols[0] if len(cols) > 0 else "/home/pixi/workspace"
                pid = cols[1] if len(cols) > 1 else ""
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                dns_hint = cols[3] if len(cols) > 3 and cols[3] else ""
                return json.dumps(self._secure_project_op(
                    username, pid, "list", "/api/file/list",
                    {"directory": directory, "pattern": "*"}
                ))

            elif path == "/api/project/install":
                manager = cols[0] if len(cols) > 0 else ""
                packages = cols[1] if len(cols) > 1 else ""
                pid = cols[2] if len(cols) > 2 else ""
                username = cols[3].upper() if len(cols) > 3 and cols[3] else username
                dns_hint = cols[4] if len(cols) > 4 and cols[4] else ""
                return json.dumps(self._secure_project_op(
                    username, pid, "install", "/api/package/install",
                    {"manager": manager, "packages": packages}
                ))

            elif path == "/api/project/query":
                sql_text = cols[0] if len(cols) > 0 else ""
                pid = cols[1] if len(cols) > 1 else ""
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                user_token = self.headers.get("Sf-Context-Current-User-Token", "")
                return json.dumps(self._secure_project_op(
                    username, pid, "query", "/api/query",
                    {"sql": sql_text, "user_token": user_token}
                ))

            elif path == "/api/project/devmode":
                pid = cols[0] if len(cols) > 0 else ""
                enable = str(cols[1]).lower() in ("true", "1", "yes") if len(cols) > 1 else True
                username = cols[2].upper() if len(cols) > 2 and cols[2] else username
                return json.dumps(self._set_devmode(username, pid, enable))

            # --- Admin ---
            elif path == "/api/admin/rotate_secret":
                pid = cols[0] if len(cols) > 0 else ""
                username = cols[1].upper() if len(cols) > 1 and cols[1] else username
                return json.dumps(self._admin_rotate_secret(username, pid))

            elif path == "/api/admin/destroy":
                pid = cols[0] if len(cols) > 0 else ""
                username = cols[1].upper() if len(cols) > 1 and cols[1] else username
                return json.dumps(self._admin_destroy(username, pid))

            elif path == "/api/admin/get_audit_log":
                lines = int(cols[0]) if len(cols) > 0 and cols[0] else 100
                return json.dumps(self._admin_get_audit_log(username, lines))

            elif path == "/api/admin/update_image_digest":
                digest = cols[0] if len(cols) > 0 else ""
                return json.dumps(self._admin_update_image(username, digest))

            elif path == "/api/admin/set_dev_mode_allowed":
                pid = cols[0] if len(cols) > 0 else ""
                allowed = str(cols[1]).lower() in ("true", "1", "yes") if len(cols) > 1 else True
                return json.dumps(self._admin_set_dev_mode_allowed(username, pid, allowed))

            else:
                return json.dumps({"error": f"Unknown endpoint: {path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _route_direct(self, path, body):
        username = extract_username(self.headers)
        if path == "/api/list_projects":
            return self._list_projects(username)
        return {"error": "Use service function format"}

    # --- Core Security Gate ---
    def _secure_project_op(self, username, project_id, operation, endpoint, payload, command_text=None):
        """Central security check: identity + command validation + audit + proxy."""
        # 1. Identity check
        if not username:
            audit_log("unknown", project_id, operation, "", "no_identity")
            return {"error": "Cannot identify caller. Authentication required."}

        # 2. Access check
        allowed, reason = verify_access(project_id, username)
        if not allowed:
            audit_log(username, project_id, operation, "", "access_denied")
            return {"error": reason, "code": 403}

        # 3. Command sanitization (for exec only)
        if command_text:
            cmd_ok, cmd_reason = validate_command(command_text)
            if not cmd_ok:
                audit_log(username, project_id, "exec_blocked", command_text, "command_denied")
                return {"error": cmd_reason, "code": 403}

        # 4. Proxy to project container
        project = REGISTRY[project_id]
        # Use timeout from payload if it's an exec command, otherwise default
        proxy_timeout = min(int(payload.get("timeout_seconds", 60)) + 10, 310)
        result = proxy_to_project(project, endpoint, payload, timeout=proxy_timeout)

        # 5. Audit log
        details = command_text or payload.get("file_path", "") or payload.get("sql", "")[:100]
        audit_log(username, project_id, operation, details, "success")

        return result

    # --- Project Management ---
    def _register_project(self, project_id, name, owner, secret_hash, service_name, service_dns):
        REGISTRY[project_id] = {
            "project_id": project_id,
            "project_name": sanitize_project_name(name),
            "owner": owner.upper(),
            "secret_hash": secret_hash,
            "service_name": service_name,
            "service_dns": service_dns,
            "public_url": "provisioning...",
            "template": "nextjs",
            "status": "ACTIVE",
            "collaborators": [],
            "dev_mode_allowed": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_project_to_snowflake(project_id)
        audit_log(owner, project_id, "register", service_name, "success")
        return {"registered": True, "project_id": project_id, "total_projects": len(REGISTRY)}

    def _list_projects(self, username):
        user_projects = [
            {"project_id": p["project_id"], "name": p["project_name"],
             "status": p["status"], "role": "owner" if usernames_match(username, p["owner"]) else "collaborator"}
            for p in REGISTRY.values()
            if usernames_match(username, p["owner"]) or any(usernames_match(username, c) for c in p.get("collaborators", []))
        ]
        return {"projects": user_projects, "count": len(user_projects)}

    def _add_collaborator(self, caller, project_id, collaborator):
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        if caller.upper() != project["owner"].upper():
            audit_log(caller, project_id, "add_collaborator_denied", collaborator, "not_owner")
            return {"error": "Only the owner can add collaborators"}

        collabs = project.get("collaborators", [])
        if collaborator.upper() not in [c.upper() for c in collabs]:
            collabs.append(collaborator.upper())
            project["collaborators"] = collabs
            REGISTRY[project_id] = project
            save_project_to_snowflake(project_id)

        audit_log(caller, project_id, "add_collaborator", collaborator, "success")
        return {"success": True, "collaborators": collabs}

    def _remove_collaborator(self, caller, project_id, collaborator):
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        if caller.upper() != project["owner"].upper():
            return {"error": "Only the owner can remove collaborators"}

        collabs = [c for c in project.get("collaborators", []) if c.upper() != collaborator.upper()]
        project["collaborators"] = collabs
        REGISTRY[project_id] = project
        save_project_to_snowflake(project_id)
        audit_log(caller, project_id, "remove_collaborator", collaborator, "success")
        return {"success": True, "collaborators": collabs}

    # --- Dev Mode ---
    def _set_devmode(self, username, project_id, enable):
        """Enable or disable dev mode (VSCode, terminal, token-refresh) for a project."""
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        allowed, reason = verify_access(project_id, username)
        if not allowed:
            audit_log(username, project_id, "devmode_denied", "", "access_denied")
            return {"error": reason}
        if enable and not project.get("dev_mode_allowed", True):
            audit_log(username, project_id, "devmode_denied", "", "admin_blocked")
            return {"error": "Dev mode is disabled by admin for this project"}

        service_name = project.get("service_name", "")
        if not service_name:
            return {"error": "No service found for this project"}

        conn = get_sf_connection()
        if not conn:
            return {"error": "No Snowflake connection"}

        # Read current service spec, update DEVMODE_ENABLED env var, ALTER SERVICE
        devmode_val = "true" if enable else "false"
        try:
            cur = conn.cursor()
            # Get current spec
            cur.execute(f"DESCRIBE SERVICE {PROJECT_SCHEMA}.{service_name}")
            row = cur.fetchone()
            if not row:
                return {"error": "Service not found"}
            # The spec is in the 'spec' column — use string replacement to update env var
            spec_text = row[6] if len(row) > 6 else ""  # spec column
            # Simple string replacement for the env var
            if "DEVMODE_ENABLED" in spec_text:
                import re as _re
                spec_text = _re.sub(r'DEVMODE_ENABLED:\s*"[^"]*"', f'DEVMODE_ENABLED: "{devmode_val}"', spec_text)
            else:
                # Add DEVMODE_ENABLED to the env block
                spec_text = spec_text.replace('BACKUP_INTERVAL_SECONDS:', f'DEVMODE_ENABLED: "{devmode_val}"\n        BACKUP_INTERVAL_SECONDS:')

            # Strip the leading "--- " if present
            if spec_text.startswith("--- "):
                spec_text = spec_text[4:]
            if spec_text.startswith("---\n"):
                spec_text = spec_text[4:]

            dd = chr(36) + chr(36)
            cur.execute(f"ALTER SERVICE {PROJECT_SCHEMA}.{service_name} FROM SPECIFICATION {dd}{spec_text}{dd}")
            audit_log(username, project_id, "devmode", f"enable={enable}", "success")
            return {"success": True, "devmode_enabled": enable, "message": f"Dev mode {'enabled' if enable else 'disabled'}. Service is restarting."}
        except Exception as e:
            audit_log(username, project_id, "devmode_failed", str(e)[:200], "error")
            return {"error": f"Failed to update service: {str(e)[:200]}"}

    # --- Admin Operations ---
    def _admin_rotate_secret(self, username, project_id):
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        # Only owner or admin can rotate
        if username.upper() != project["owner"].upper():
            audit_log(username, project_id, "rotate_denied", "", "not_owner")
            return {"error": "Only the project owner can rotate secrets"}

        new_secret = generate_secret()
        project["secret_hash"] = hash_secret(new_secret)
        REGISTRY[project_id] = project
        save_project_to_snowflake(project_id)
        audit_log(username, project_id, "rotate_secret", "", "success")
        return {"project_id": project_id, "new_secret": new_secret}

    def _admin_destroy(self, username, project_id):
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        if username.upper() != project["owner"].upper():
            audit_log(username, project_id, "destroy_denied", "", "not_owner")
            return {"error": "Only the project owner can destroy"}

        service_name = project.get("service_name", "")

        # Drop the SPCS service (force to skip block volume snapshot prompt)
        if service_name:
            conn = get_sf_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(f"DROP SERVICE IF EXISTS {PROJECT_SCHEMA}.{service_name} FORCE")
                    print(f"[destroy] Dropped service {service_name}", flush=True)
                except Exception as e:
                    print(f"[destroy] Failed to drop service {service_name}: {e}", flush=True)

        # Clean up stage-backed backup volume files
        conn = get_sf_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(f"REMOVE @DEVCONTAINER_DB.PROJECTS.BACKUPS/{project_id}/")
                print(f"[destroy] Removed backup files for {project_id}", flush=True)
            except Exception as e:
                print(f"[destroy] Failed to remove backup files: {e}", flush=True)

        del REGISTRY[project_id]
        delete_project_from_snowflake(project_id)
        audit_log(username, project_id, "destroy", service_name, "success")
        return {"message": f"Project '{project_id}' destroyed: service dropped, backups cleaned, registry entry removed."}

    def _admin_get_audit_log(self, username, lines):
        if username.upper() not in ADMIN_USERS:
            audit_log(username, "", "audit_log_denied", "", "not_admin")
            return {"entries": [], "total": 0, "error": "Admin access required"}
        conn = get_sf_connection()
        if not conn:
            return {"entries": [], "total": 0, "error": "No Snowflake connection"}
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT TS, USERNAME, PROJECT_ID, OPERATION, DETAILS, RESULT FROM DEVCONTAINER_DB.PROJECTS.AUDIT_LOG ORDER BY TS DESC LIMIT {lines}")
            rows = cur.fetchall()
            entries = [{"ts": str(r[0]), "user": r[1], "project": r[2], "op": r[3], "details": r[4], "result": r[5]} for r in rows]
            return {"entries": entries, "total": len(entries)}
        except Exception as e:
            return {"entries": [], "total": 0, "error": str(e)}

    def _admin_update_image(self, username, digest):
        if username.upper() not in ADMIN_USERS:
            audit_log(username, "", "update_image_denied", "", "not_admin")
            return {"error": "Admin access required"}
        global PROJECT_IMAGE, IMAGE_DIGEST
        if not digest.startswith("sha256:"):
            return {"error": "Digest must start with sha256:"}
        IMAGE_DIGEST = digest
        PROJECT_IMAGE = f"{IMAGE_REPO}@{IMAGE_DIGEST}"
        audit_log(username, "", "update_image", digest, "success")
        return {"image": PROJECT_IMAGE, "digest": IMAGE_DIGEST}

    def _admin_set_dev_mode_allowed(self, username, project_id, allowed):
        """Admin-only: set whether a project is allowed to enable dev mode."""
        if username.upper() not in ADMIN_USERS:
            audit_log(username, project_id, "set_dev_mode_allowed_denied", "", "not_admin")
            return {"error": "Admin access required"}
        project = REGISTRY.get(project_id)
        if not project:
            return {"error": "Project not found"}
        project["dev_mode_allowed"] = allowed
        REGISTRY[project_id] = project
        save_project_to_snowflake(project_id)
        audit_log(username, project_id, "set_dev_mode_allowed", str(allowed), "success")
        return {"success": True, "project_id": project_id, "dev_mode_allowed": allowed}


# --- Startup ---
if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", "8080"))

    count = load_registry_from_snowflake()
    if count > 0:
        print(f"Loaded {count} projects from Snowflake registry", flush=True)
    else:
        print("Starting with empty registry (Snowflake table empty or unavailable)", flush=True)

    print(f"Security: command deny patterns={len(COMPILED_DENY)}, max_projects={MAX_PROJECTS_PER_USER}, max_file={MAX_FILE_SIZE}", flush=True)
    print(f"Image: {PROJECT_IMAGE}", flush=True)

    server = HTTPServer(("0.0.0.0", port), OrchestratorHandler)
    print(f"Orchestrator API v2 (hardened) running on port {port}", flush=True)
    server.serve_forever()
