"""
SPCS DevContainer API Server
Pure stdlib implementation — no external dependencies needed.
"""
import json
import os
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/pixi/workspace")
MAX_OUTPUT_BYTES = 200_000


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

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json({"status": "ok"})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

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
        return {"error": f"unknown endpoint: {path}"}

    def _exec_command(self, command, working_dir, timeout):
        try:
            os.makedirs(working_dir, exist_ok=True)
            env = {**os.environ, "HOME": "/home/pixi", "PATH": "/home/pixi/projects/.pixi/envs/default/bin:/home/pixi/.pixi/bin:/home/pixi/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
            result = subprocess.run(
                ["bash", "-lc", command],
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
            "pip": f"/home/pixi/projects/.pixi/envs/default/bin/python3.14 -m pip install {packages}",
            "conda": f"/home/pixi/.pixi/bin/pixi global install {packages}",
        }
        try:
            env = {**os.environ, "HOME": "/home/pixi", "PATH": "/home/pixi/projects/.pixi/envs/default/bin:/home/pixi/.pixi/bin:/home/pixi/.local/bin:/usr/local/bin:/usr/bin:/bin"}
            result = subprocess.run(
                ["bash", "-lc", commands[manager]],
                capture_output=True, text=True, timeout=180, env=env,
            )
            output = (result.stdout + result.stderr)[:MAX_OUTPUT_BYTES]
            return json.dumps({"success": result.returncode == 0, "output": output, "exit_code": result.returncode, "error": None if result.returncode == 0 else "Install failed"})
        except subprocess.TimeoutExpired:
            return json.dumps({"success": False, "output": "", "exit_code": -1, "error": "Timed out"})
        except Exception as e:
            return json.dumps({"success": False, "output": "", "exit_code": -1, "error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("API_PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), APIHandler)
    print(f"API server running on port {port}", flush=True)
    server.serve_forever()
