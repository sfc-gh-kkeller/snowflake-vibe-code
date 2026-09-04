#!/usr/bin/env python3
"""
Token refresh daemon for SPCS devcontainers.
Keeps ~/.snowflake/connections.toml and ~/.snowflake/token up to date
so that `cortex`, `snow` CLI, and VS Code extensions work without manual auth.

Auth priority:
1. PAT (SNOWFLAKE_PAT env var) — works always, best for unattended backup + CLI
2. Caller-rights combined token (<service-oauth-token>.<caller-token>) — user identity.
   Matches Snowflake's documented caller-rights format for SPCS, see
   "Snowpark Container Services: SQL execution" in the Snowflake docs.
3. Service OAuth token (/snowflake/session/token) — fallback, service identity.

Notes:
- A bare caller-rights token is NEVER a valid standalone OAuth token; it only
  works when combined with the service token (service.caller).
- The service/caller tokens only authenticate against the SPCS account that
  issued them, so the target account is derived from the service token's
  issuer rather than trusting SNOWFLAKE_ACCOUNT/SNOWFLAKE_HOST blindly.
- A candidate token is only written after it verifies by opening a real
  session, so a broken token is never left on disk.
"""
import json
import os
import signal
import sys
import time

HOME = os.environ.get("HOME", "/home/pixi")
SNOWFLAKE_DIR = os.path.join(HOME, ".snowflake")
TOKEN_FILE = os.path.join(SNOWFLAKE_DIR, "token")
CONNECTIONS_FILE = os.path.join(SNOWFLAKE_DIR, "connections.toml")
SERVICE_TOKEN_PATH = "/snowflake/session/token"
CALLER_TOKENS_DIR = "/tmp/caller_tokens"
# The caller-rights token is only valid for ~2 minutes (default
# SERVICE_CALLER_TOKEN_VALIDITY_SECS). Only trust one while it still has at
# least this much life left, otherwise the file would end up holding an
# already-expired token. The browser keep-alive tab refreshes every ~25s but
# background tabs are often throttled to ~1/min.
CALLER_TOKEN_MAX_AGE = 90
CALLER_TOKEN_MIN_REMAINING = 30
REFRESH_INTERVAL = 30  # seconds

running = True


def signal_handler(signum, frame):
    global running
    running = False
    sys.exit(0)


def read_service_token():
    try:
        with open(SERVICE_TOKEN_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def read_caller_token():
    """Read the freshest valid caller-rights token from per-user token files.

    Tokens are stored per-user at /tmp/caller_tokens/{USER}.json by the
    api-server. We scan all files and pick the freshest one that hasn't expired.
    """
    if not os.path.isdir(CALLER_TOKENS_DIR):
        return ""
    best_token = ""
    best_ts = 0
    for fname in os.listdir(CALLER_TOKENS_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(CALLER_TOKENS_DIR, fname)
        try:
            age = time.time() - os.stat(fpath).st_mtime
            if age > CALLER_TOKEN_MAX_AGE:
                continue
            with open(fpath) as f:
                data = json.loads(f.read())
            token = data.get("token", "")
            ts = data.get("ts", 0)
            if not token or ts <= best_ts:
                continue
            claims = jwt_claims(token)
            exp = claims.get("exp")
            if isinstance(exp, (int, float)):
                remaining = exp - time.time()
                if remaining < CALLER_TOKEN_MIN_REMAINING:
                    continue
            best_token = token
            best_ts = ts
        except (json.JSONDecodeError, OSError):
            continue
    if best_token:
        user = ""
        try:
            for fname in os.listdir(CALLER_TOKENS_DIR):
                fpath = os.path.join(CALLER_TOKENS_DIR, fname)
                with open(fpath) as f:
                    d = json.loads(f.read())
                if d.get("token") == best_token:
                    user = d.get("user", fname.replace(".json", ""))
                    break
        except Exception:
            pass
        if user:
            print(f"[token-refresh] Using caller token from {user}", flush=True)
    return best_token


def jwt_claims(token):
    """Decode the payload of a JWT (unverified) as a dict, or {} on error."""
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def hosting_account_from_service_token(service_token):
    """Derive the SPCS account that issued the service token, if possible.

    The service OAuth token is only valid in the account that minted it, so the
    host/account from its `iss`/`aud` claim is the authoritative target.
    Returns (account, host) or None.
    """
    if not service_token:
        return None
    claims = jwt_claims(service_token)
    iss = claims.get("iss") or claims.get("aud") or ""
    iss = iss.replace("https://", "").replace("http://", "").strip("/")
    if not iss or "." not in iss:
        return None
    return (iss.split(".")[0], iss)


def verify_token(account, host, token):
    """Return True if the token can actually open a Snowflake session."""
    if not account or not host or not token:
        return False
    try:
        import snowflake.connector
    except ImportError:
        return True  # can't verify locally; don't block writes
    try:
        conn = snowflake.connector.connect(
            account=account,
            host=host,
            token=token,
            authenticator="oauth",
            login_timeout=10,
        )
        try:
            conn.cursor().execute("SELECT CURRENT_ACCOUNT()")
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"[token-refresh] verify failed for {account}: {str(e)[:160]}", flush=True)
        return False


def write_token(token):
    """Write the active token to ~/.snowflake/token."""
    os.makedirs(SNOWFLAKE_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)


def write_connections_toml(auth_method, auth_value, account, host, warehouse):
    """Generate connections.toml with the best available auth.

    auth_method: "pat" or "oauth_token_file"
    auth_value: the PAT string or token file path
    """
    user = os.environ.get("SNOWFLAKE_USER", "")

    if not account or not host:
        return

    os.makedirs(SNOWFLAKE_DIR, exist_ok=True)

    if auth_method == "pat":
        content = f"""[default]
account = "{account}"
host = "{host}"
password = "{auth_value}"
warehouse = "{warehouse}"
"""
    else:
        content = f"""[default]
account = "{account}"
host = "{host}"
authenticator = "OAUTH"
token_file_path = "{auth_value}"
warehouse = "{warehouse}"
"""

    if user:
        content += f'user = "{user}"\n'

    # Only write if changed
    existing = ""
    try:
        with open(CONNECTIONS_FILE) as f:
            existing = f.read()
    except FileNotFoundError:
        pass

    if existing != content:
        with open(CONNECTIONS_FILE, "w") as f:
            f.write(content)
        os.chmod(CONNECTIONS_FILE, 0o600)
        print(f"[token-refresh] Updated connections.toml (method={auth_method})", flush=True)


def refresh():
    """One refresh cycle: pick best auth, verify it, write files."""
    pat = os.environ.get("SNOWFLAKE_PAT", "")

    if pat:
        write_connections_toml(
            "pat",
            pat,
            os.environ.get("SNOWFLAKE_ACCOUNT", ""),
            os.environ.get("SNOWFLAKE_HOST", ""),
            os.environ.get("QUERY_WAREHOUSE", ""),
        )
        return

    service_token = read_service_token()
    caller_token = read_caller_token()

    # Candidate login tokens, in priority order:
    #   1. combined service.caller (caller-rights / user identity)
    #   2. service token alone (service identity)
    candidates = []
    if service_token and caller_token:
        candidates.append(("combined_caller_rights", f"{service_token}.{caller_token}"))
    if service_token:
        candidates.append(("service_token", service_token))
    if not candidates:
        print("[token-refresh] No service or caller token available", flush=True)
        return

    # Candidate target accounts, in priority order:
    #   1. the SPCS account that issued the service token (authoritative)
    #   2. env-configured account (legacy/compat)
    accounts = []
    hosting = hosting_account_from_service_token(service_token)
    if hosting:
        accounts.append(hosting)
    env_account = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    env_host = os.environ.get("SNOWFLAKE_HOST", "")
    if env_account and env_host and (env_account, env_host) not in accounts:
        accounts.append((env_account, env_host))

    warehouse = os.environ.get("QUERY_WAREHOUSE", "")

    for label, token in candidates:
        for account, host in accounts:
            if verify_token(account, host, token):
                print(f"[token-refresh] Verified auth: {label} -> {account}", flush=True)
                write_token(token)
                write_connections_toml("oauth_token_file", TOKEN_FILE, account, host, warehouse)
                return

    print("[token-refresh] No working auth combination found; leaving files unchanged", flush=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pat = os.environ.get("SNOWFLAKE_PAT", "")
    mode = "PAT" if pat else "OAuth (service/caller token)"
    print(f"[token-refresh] Started. Interval={REFRESH_INTERVAL}s, auth={mode}", flush=True)

    # Initial setup
    refresh()

    while running:
        time.sleep(REFRESH_INTERVAL)
        if running:
            refresh()
