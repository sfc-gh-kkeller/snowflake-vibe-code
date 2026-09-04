"""
SPCS DevContainer API Server
Pure stdlib implementation — no external dependencies needed.
"""
import json
import os
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/pixi/workspace")
MAX_OUTPUT_BYTES = 200_000

COCOAUTH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CoCo Auth</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; color: #e0e0e0; background: #1a1a2e; }
  h1 { color: #29b5e8; }
  .status { padding: 16px; border-radius: 8px; margin: 20px 0; }
  .ok { background: #1b3a2a; border: 1px solid #2ecc71; }
  .err { background: #3a1b1b; border: 1px solid #e74c3c; }
  .info { color: #999; font-size: 0.9em; margin-top: 24px; }
  #log { font-family: monospace; font-size: 0.85em; color: #888; max-height: 200px; overflow-y: auto; margin-top: 12px; }
  .pulse { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #2ecc71; margin-right: 8px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }
</style>
</head>
<body>
<h1>CoCo Auth</h1>
<div id="box" class="status ok">
  <span class="pulse"></span>
  <strong>User:</strong> {{USER}}<br>
  <strong>Status:</strong> <span id="st">{{STATUS}}</span><br>
  <strong>Last refresh:</strong> <span id="lr">just now</span>
</div>
<p class="info">Keep this tab open. It refreshes your caller-rights token every 25 seconds so the VS Code Snowflake extension and CoCo CLI stay authenticated inside the container.</p>
<div id="log"></div>
<script>
let count = 0;
function log(msg) {
  const d = document.getElementById('log');
  const t = new Date().toLocaleTimeString();
  d.innerHTML = '<div>[' + t + '] ' + msg + '</div>' + d.innerHTML;
  if (d.children.length > 50) d.removeChild(d.lastChild);
}
async function refresh() {
  try {
    const r = await fetch('/api/cocoauth/refresh', {method:'POST'});
    const j = await r.json();
    count++;
    document.getElementById('st').textContent = j.ok ? 'Token active' : 'No token in header';
    document.getElementById('lr').textContent = new Date().toLocaleTimeString() + ' (#' + count + ')';
    document.getElementById('box').className = 'status ' + (j.ok ? 'ok' : 'err');
    log(j.ok ? 'Token refreshed for ' + (j.user||'?') : 'No token received');
  } catch(e) {
    document.getElementById('box').className = 'status err';
    log('Refresh error: ' + e.message);
  }
}
setInterval(refresh, 25000);
log('Auto-refresh started (every 25s)');
</script>
</body>
</html>
"""


class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging to keep logs clean
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

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _capture_caller_token(self):
        """Capture caller-rights token from SPCS ingress header, keyed by user.
        Tokens are stored per-user at /tmp/caller_tokens/{USER}.json so that
        collaborators sharing a project don't overwrite each other's identity."""
        caller_token = self.headers.get("Sf-Context-Current-User-Token", "")
        caller_user = self.headers.get("Sf-Context-Current-User", "")
        if caller_token and caller_user:
            try:
                os.makedirs("/tmp/caller_tokens", exist_ok=True)
                safe_name = caller_user.upper().replace("/", "_").replace("..", "_")
                path = f"/tmp/caller_tokens/{safe_name}.json"
                with open(path, "w") as f:
                    f.write(json.dumps({"token": caller_token, "ts": time.time(), "user": caller_user}))
                return caller_user, True
            except Exception:
                pass
        return caller_user, False

    @staticmethod
    def _read_freshest_caller_token():
        """Read the most recently written caller token across all users."""
        token_dir = "/tmp/caller_tokens"
        if not os.path.isdir(token_dir):
            return None
        best = None
        for fname in os.listdir(token_dir):
            if not fname.endswith(".json"):
                continue
            try:
                fpath = os.path.join(token_dir, fname)
                with open(fpath) as f:
                    data = json.loads(f.read())
                if best is None or data.get("ts", 0) > best.get("ts", 0):
                    best = data
            except Exception:
                continue
        return best

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"status": "ok"})
        elif path == "/cocoauth":
            caller_user, ok = self._capture_caller_token()
            self._send_html(COCOAUTH_HTML.replace("{{USER}}", caller_user or "unknown")
                                         .replace("{{STATUS}}", "Token captured" if ok else "No token in header"))
        elif path == "/api/cocoauth/status":
            data = self._read_freshest_caller_token()
            if data:
                age = time.time() - data.get("ts", 0)
                self._send_json({"ok": True, "age_seconds": round(age, 1), "has_token": bool(data.get("token")), "user": data.get("user", "")})
            else:
                self._send_json({"ok": False, "age_seconds": -1, "has_token": False})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        # /cocoauth/refresh — browser keepalive for caller token
        if path == "/api/cocoauth/refresh":
            caller_user, ok = self._capture_caller_token()
            self._send_json({"ok": ok, "user": caller_user, "ts": time.time()})
            return

        body = self._read_body()

        # Cache caller-rights token if present (for token_refresh daemon)
        self._capture_caller_token()

        # Service function sends data as a batch with "data" key containing rows
        # Each row is [[col1, col2, ...]] format for service functions
        # Or direct JSON for testing
        if "data" in body:
            # Service function batch format: {"data": [[row_index, col1, col2, ...]]}
            rows = body["data"]
            results = []
            for row in rows:
                row_idx = row[0]  # First element is always the row index
                cols = row[1:]    # Actual function arguments start at index 1
                result = self._handle_request(path, cols)
                results.append([row_idx, result])
            self._send_json({"data": results})
        else:
            # Direct call format (for testing)
            result = self._handle_request_direct(path, body)
            self._send_json(result)

    def _handle_request(self, path, cols):
        """Handle a service function batch row (cols = arguments without row index)."""
        try:
            if path == "/api/exec":
                command = cols[0] if len(cols) > 0 else ""
                working_dir = cols[1] if len(cols) > 1 and cols[1] else WORKSPACE_DIR
                timeout = int(cols[2]) if len(cols) > 2 and cols[2] else 30
                return self._exec_command(command, working_dir, timeout)
            elif path == "/api/file/read":
                file_path = cols[0] if len(cols) > 0 else ""
                offset = int(cols[1]) if len(cols) > 1 and cols[1] is not None else 0
                max_lines = int(cols[2]) if len(cols) > 2 and cols[2] is not None else 2000
                return self._read_file(file_path, offset, max_lines)
            elif path == "/api/file/write":
                file_path = cols[0] if len(cols) > 0 else ""
                content = cols[1] if len(cols) > 1 else ""
                return self._write_file(file_path, content)
            elif path == "/api/file/edit":
                file_path = cols[0] if len(cols) > 0 else ""
                old_string = cols[1] if len(cols) > 1 else ""
                new_string = cols[2] if len(cols) > 2 else ""
                return self._edit_file(file_path, old_string, new_string)
            elif path == "/api/file/list":
                directory = cols[0] if len(cols) > 0 and cols[0] else WORKSPACE_DIR
                pattern = cols[1] if len(cols) > 1 and cols[1] else "*"
                return self._list_files(directory, pattern)
            elif path == "/api/package/install":
                manager = cols[0] if len(cols) > 0 else ""
                packages = cols[1] if len(cols) > 1 else ""
                return self._install_package(manager, packages)
            else:
                return json.dumps({"error": f"unknown endpoint: {path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _handle_request_direct(self, path, body):
        """Handle a direct JSON request (for testing)."""
        if path == "/api/exec":
            return self._exec_command(
                body.get("command", ""),
                body.get("working_dir", WORKSPACE_DIR),
                body.get("timeout_seconds", 30),
            )
        elif path == "/api/file/read":
            return self._read_file(
                body.get("file_path", ""),
                body.get("offset", 0),
                body.get("max_lines", 2000),
            )
        elif path == "/api/file/write":
            return self._write_file(body.get("file_path", ""), body.get("content", ""))
        elif path == "/api/file/edit":
            return self._edit_file(
                body.get("file_path", ""),
                body.get("old_string", ""),
                body.get("new_string", ""),
            )
        elif path == "/api/file/list":
            return self._list_files(
                body.get("directory", WORKSPACE_DIR),
                body.get("pattern", "*"),
            )
        elif path == "/api/package/install":
            return self._install_package(
                body.get("manager", ""), body.get("packages", "")
            )
        elif path == "/api/query":
            return self._query_snowflake(
                body.get("sql", ""), body.get("user_token", "")
            )
        return {"error": f"unknown endpoint: {path}"}

    def _exec_command(self, command, working_dir, timeout):
        try:
            os.makedirs(working_dir, exist_ok=True)
            env = {**os.environ, "HOME": "/home/pixi", "PATH": "/home/pixi/.pixi/bin:/home/pixi/.local/bin:/usr/local/bin:/usr/bin:/bin:/home/pixi/workspace/node_modules/.bin"}
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            stdout = result.stdout
            truncated = False
            if len(stdout) > MAX_OUTPUT_BYTES:
                stdout = stdout[:MAX_OUTPUT_BYTES] + "\n... [truncated]"
                truncated = True
            return json.dumps({
                "stdout": stdout,
                "stderr": result.stderr[:50000],
                "exit_code": result.returncode,
                "truncated": truncated,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1, "truncated": False})
        except Exception as e:
            return json.dumps({"stdout": "", "stderr": str(e), "exit_code": -1, "truncated": False})

    def _read_file(self, file_path, offset, max_lines):
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"error": f"File not found: {file_path}", "content": None})
        if not path.is_file():
            return json.dumps({"error": f"Not a file: {file_path}", "content": None})
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            selected = lines[offset:offset + max_lines]
            numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)]
            content = "\n".join(numbered)
            if len(content) > MAX_OUTPUT_BYTES:
                content = content[:MAX_OUTPUT_BYTES] + "\n... [truncated]"
            return json.dumps({"content": content, "total_lines": total, "lines_returned": len(selected), "error": None})
        except Exception as e:
            return json.dumps({"error": str(e), "content": None})

    def _write_file(self, file_path, content):
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return json.dumps({"success": True, "bytes_written": len(content.encode("utf-8")), "error": None})
        except Exception as e:
            return json.dumps({"success": False, "bytes_written": 0, "error": str(e)})

    def _edit_file(self, file_path, old_string, new_string):
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"success": False, "error": f"File not found: {file_path}"})
        try:
            content = path.read_text(encoding="utf-8")
            if old_string not in content:
                return json.dumps({"success": False, "error": "old_string not found in file"})
            count = content.count(old_string)
            if count > 1:
                return json.dumps({"success": False, "error": f"old_string found {count} times - must be unique"})
            new_content = content.replace(old_string, new_string, 1)
            path.write_text(new_content, encoding="utf-8")
            return json.dumps({"success": True, "error": None})
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)})

    def _list_files(self, directory, pattern):
        dir_path = Path(directory)
        if not dir_path.exists():
            return json.dumps({"error": f"Directory not found: {directory}", "files": []})
        try:
            matches = sorted(dir_path.glob(pattern))[:500]
            entries = []
            for p in matches:
                try:
                    stat = p.stat()
                    entries.append({"name": p.name, "path": str(p), "is_dir": p.is_dir(), "size": stat.st_size if p.is_file() else None})
                except OSError:
                    continue
            return json.dumps({"files": entries, "total": len(entries), "error": None})
        except Exception as e:
            return json.dumps({"error": str(e), "files": []})

    def _install_package(self, manager, packages):
        allowed = {"pixi", "npm", "pip", "conda"}
        if manager not in allowed:
            return json.dumps({"success": False, "error": f"Use: {allowed}", "output": ""})
        commands = {
            "pixi": f"/home/pixi/.pixi/bin/pixi global install {packages}",
            "npm": f"npm install -g {packages}",
            "pip": f"python3 -m pip install --break-system-packages {packages}",
            "conda": f"/home/pixi/.pixi/bin/pixi global install {packages}",
        }
        try:
            env = {**os.environ, "HOME": "/home/pixi", "PATH": "/home/pixi/.pixi/bin:/home/pixi/.local/bin:/usr/local/bin:/usr/bin:/bin:/home/pixi/workspace/node_modules/.bin"}
            result = subprocess.run(
                ["bash", "-c", commands[manager]],
                capture_output=True, text=True, timeout=180, env=env,
            )
            output = (result.stdout + result.stderr)[:MAX_OUTPUT_BYTES]
            return json.dumps({"success": result.returncode == 0, "output": output, "exit_code": result.returncode, "error": None if result.returncode == 0 else "Install failed"})
        except subprocess.TimeoutExpired:
            return json.dumps({"success": False, "output": "", "exit_code": -1, "error": "Timed out"})
        except Exception as e:
            return json.dumps({"success": False, "output": "", "exit_code": -1, "error": str(e)})

    def _query_snowflake(self, sql, user_token):
        """Execute SQL as the calling user via caller rights token."""
        if not sql:
            return json.dumps({"error": "No SQL provided", "data": None})

        # Get service OAuth token
        service_token = ""
        try:
            with open("/snowflake/session/token") as f:
                service_token = f.read().strip()
        except FileNotFoundError:
            return json.dumps({"error": "No service token available", "data": None})

        if not user_token:
            return json.dumps({"error": "No user token provided. Caller rights require the user token.", "data": None})

        # Construct combined login token for caller rights
        # Format: <service_oauth_token>.<user_token>
        combined_token = f"{service_token}.{user_token}"

        try:
            import snowflake.connector
            import base64 as _b64

            # Derive hosting account from service token JWT issuer
            # (SPCS tokens only authenticate against the account that issued them)
            host = os.environ.get("SNOWFLAKE_HOST", "")
            account = os.environ.get("SNOWFLAKE_ACCOUNT", "")
            try:
                payload = service_token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(_b64.urlsafe_b64decode(payload))
                iss = claims.get("iss") or claims.get("aud") or ""
                iss = iss.replace("https://", "").replace("http://", "").strip("/")
                if iss and "." in iss:
                    account = iss.split(".")[0]
                    host = iss
            except Exception:
                pass

            conn = snowflake.connector.connect(
                host=host,
                account=account,
                token=combined_token,
                authenticator="oauth",
                warehouse=os.environ.get("QUERY_WAREHOUSE", ""),
            )
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchmany(100)  # Limit to 100 rows
            data = [dict(zip(columns, row)) for row in rows]
            conn.close()

            result = json.dumps({"columns": columns, "data": data, "row_count": len(data), "error": None}, default=str)
            if len(result) > MAX_OUTPUT_BYTES:
                result = result[:MAX_OUTPUT_BYTES]
            return result
        except ImportError:
            return json.dumps({"error": "snowflake-connector-python not installed in this container", "data": None})
        except Exception as e:
            return json.dumps({"error": str(e), "data": None})


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", "9090"))
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"API server running on port {port}", flush=True)
    server.serve_forever()
