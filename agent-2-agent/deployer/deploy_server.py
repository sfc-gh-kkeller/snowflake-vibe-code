"""
SAR Deployer Service — lightweight SPCS container that runs `snow app deploy`.
Downloads app files from a shared stage, deploys to SAR, returns the URL.
"""
import json
import os
import subprocess
import tempfile
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("API_PORT", "8080"))
TOKEN_PATH = "/snowflake/session/token"


def get_sf_connection():
    """Create a Snowflake connection using the service token."""
    import snowflake.connector
    with open(TOKEN_PATH) as f:
        token = f.read().strip()
    # Derive hosting account from JWT
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    iss = claims.get("iss") or claims.get("aud") or ""
    iss = iss.replace("https://", "").replace("http://", "").strip("/")
    account = iss.split(".")[0] if "." in iss else ""
    host = iss
    return snowflake.connector.connect(
        account=account, host=host,
        token=token, authenticator="oauth",
        database="DEVCONTAINER_DB", schema="SPCS",
        warehouse="DEVCONTAINER_WH",
    )


class DeployHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[deployer] {args[0]}", flush=True)

    def do_GET(self):
        if self.path == "/api/health":
            self._json({"status": "ok", "service": "sar-deployer"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._read()
        if "data" in body:
            results = []
            for row in body["data"]:
                idx = row[0]
                cols = row[1:]
                result = self._deploy(cols)
                results.append([idx, result])
            self._json({"data": results})
        else:
            result = self._deploy_direct(body)
            self._json(result)

    def _deploy(self, cols):
        app_name = cols[0] if len(cols) > 0 else ""
        stage_path = cols[1] if len(cols) > 1 else ""
        caller = cols[2] if len(cols) > 2 else ""
        return json.dumps(self._run_deploy(app_name, stage_path, caller))

    def _deploy_direct(self, body):
        return self._run_deploy(
            body.get("app_name", ""),
            body.get("workspace_path", ""),
            body.get("caller", ""),
        )

    def _run_deploy(self, app_name, stage_path, caller):
        if not app_name:
            return {"error": "app_name is required"}
        if not stage_path:
            stage_path = f"@DEVCONTAINER_DB.SPCS.APP_BUILDS/{app_name}/"

        print(f"[deployer] Deploy: app={app_name} stage={stage_path} caller={caller}", flush=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = os.path.join(tmpdir, app_name)
            os.makedirs(app_dir, exist_ok=True)

            # Step 1: Download files from stage preserving directory structure
            try:
                conn = get_sf_connection()
                cur = conn.cursor()
                # List all files to get their paths
                cur.execute(f"LIST {stage_path}")
                stage_files = cur.fetchall()
                # Each row[0] is like: app_builds/hello-sar/app/layout.tsx
                # The stage_path is like: @DEVCONTAINER_DB.SPCS.APP_BUILDS/hello-sar/
                # We need to GET each file individually, preserving the subdirectory
                
                for sf in stage_files:
                    full_name = sf[0]  # e.g. app_builds/hello-sar/app/page.tsx
                    # Extract path relative to the app root
                    # Find the app_name in the path and take everything after it
                    parts = full_name.split("/")
                    try:
                        idx = parts.index(app_name)
                        rel_path = "/".join(parts[idx + 1:])  # e.g. app/page.tsx or app.yml
                    except ValueError:
                        rel_path = parts[-1]
                    
                    local_file_dir = os.path.join(app_dir, os.path.dirname(rel_path)) if "/" in rel_path else app_dir
                    os.makedirs(local_file_dir, exist_ok=True)
                    
                    # GET uses the stage-relative path (without the stage name prefix)
                    # LIST returns "app_builds/hello-sar/..." but GET needs "@STAGE_NAME/hello-sar/..."
                    get_path = f"{stage_path.rstrip('/')}/{rel_path}"
                    cur.execute(f"GET {get_path} file://{local_file_dir}/")
                
                conn.close()
                print(f"[deployer] Downloaded {len(stage_files)} files to {app_dir}", flush=True)
            except Exception as e:
                return {"error": f"Failed to download from stage: {str(e)[:500]}"}

            # Step 2: List what we got
            files = []
            for root, dirs, fnames in os.walk(app_dir):
                for fn in fnames:
                    files.append(os.path.relpath(os.path.join(root, fn), app_dir))
            print(f"[deployer] Files: {files}", flush=True)

            if not files:
                return {"error": "No files downloaded from stage"}

            # Step 3: Check app.yml exists
            app_yml = os.path.join(app_dir, "app.yml")
            if not os.path.exists(app_yml):
                return {"error": "No app.yml found in the staged files", "files": files}

            # Step 4: Run snow app deploy
            try:
                result = subprocess.run(
                    ["snow", "app", "deploy", "--connection", "default"],
                    capture_output=True, text=True, timeout=600,
                    cwd=app_dir,
                )
                output = result.stdout + result.stderr
                print(f"[deployer] snow app deploy exit={result.returncode}", flush=True)
                print(f"[deployer] output: {output[:1000]}", flush=True)

                if result.returncode == 0:
                    url = ""
                    for line in output.split("\n"):
                        if "snowflakecomputing.app" in line or "deployed" in line.lower():
                            url = line.strip()
                            if "snowflakecomputing.app" in url:
                                break
                    return {"success": True, "app_name": app_name, "url": url, "output": output[:2000]}
                else:
                    return {"error": f"snow app deploy failed (exit {result.returncode}): {output[:2000]}", "files": files}
            except subprocess.TimeoutExpired:
                return {"error": "Deploy timed out after 600s"}
            except Exception as e:
                return {"error": f"Deploy exception: {str(e)[:500]}"}

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))


if __name__ == "__main__":
    # Configure snow CLI auth at startup
    sf_dir = os.path.expanduser("~/.snowflake")
    os.makedirs(sf_dir, exist_ok=True)
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH) as f:
            token = f.read().strip()
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            iss = claims.get("iss") or claims.get("aud") or ""
            iss = iss.replace("https://", "").replace("http://", "").strip("/")
            account = iss.split(".")[0] if "." in iss else ""
            host = iss
        except Exception:
            account = ""
            host = ""
        with open(os.path.join(sf_dir, "connections.toml"), "w") as f:
            f.write(f'[default]\naccount = "{account}"\nhost = "{host}"\nauthenticator = "OAUTH"\ntoken_file_path = "{TOKEN_PATH}"\nwarehouse = "DEVCONTAINER_WH"\n')
        os.chmod(os.path.join(sf_dir, "connections.toml"), 0o600)
        print(f"[deployer] Configured snow CLI: account={account} host={host}", flush=True)

    # Verify snow CLI works
    result = subprocess.run(["snow", "--version"], capture_output=True, text=True)
    print(f"[deployer] {result.stdout.strip()}", flush=True)

    server = HTTPServer(("0.0.0.0", PORT), DeployHandler)
    print(f"[deployer] SAR Deployer running on port {PORT}", flush=True)
    server.serve_forever()
