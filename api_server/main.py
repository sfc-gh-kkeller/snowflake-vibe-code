import asyncio
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from pydantic import BaseModel

app = FastAPI(title="SPCS DevContainer API")

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/pixi/workspace")


class ExecRequest(BaseModel):
    command: str
    working_dir: str = WORKSPACE_DIR
    timeout_seconds: int = 30


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool = False


class ReadFileRequest(BaseModel):
    file_path: str
    offset: int = 0
    max_lines: int = 2000


class WriteFileRequest(BaseModel):
    file_path: str
    content: str


class EditFileRequest(BaseModel):
    file_path: str
    old_string: str
    new_string: str


class ListFilesRequest(BaseModel):
    directory: str = WORKSPACE_DIR
    pattern: str = "*"


class InstallPackageRequest(BaseModel):
    manager: str
    packages: str


MAX_OUTPUT_BYTES = 200_000  # stay under 250KB MCP limit with headroom


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/exec")
async def exec_command(req: ExecRequest):
    try:
        result = subprocess.run(
            ["bash", "-lc", req.command],
            cwd=req.working_dir,
            capture_output=True,
            text=True,
            timeout=req.timeout_seconds,
            env={**os.environ, "HOME": "/home/pixi"},
        )
        stdout = result.stdout
        stderr = result.stderr
        truncated = False

        if len(stdout) > MAX_OUTPUT_BYTES:
            stdout = stdout[:MAX_OUTPUT_BYTES] + "\n... [truncated]"
            truncated = True

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "truncated": truncated,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Command timed out after {req.timeout_seconds}s",
            "exit_code": -1,
            "truncated": False,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "truncated": False,
        }


@app.post("/api/file/read")
async def read_file(req: ReadFileRequest):
    path = Path(req.file_path)
    if not path.exists():
        return {"error": f"File not found: {req.file_path}", "content": None}
    if not path.is_file():
        return {"error": f"Not a file: {req.file_path}", "content": None}

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        selected = lines[req.offset : req.offset + req.max_lines]
        numbered = [
            f"{i + req.offset + 1}\t{line}" for i, line in enumerate(selected)
        ]
        content = "\n".join(numbered)

        if len(content) > MAX_OUTPUT_BYTES:
            content = content[:MAX_OUTPUT_BYTES] + "\n... [truncated]"

        return {
            "content": content,
            "total_lines": total_lines,
            "lines_returned": len(selected),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "content": None}


@app.post("/api/file/write")
async def write_file(req: WriteFileRequest):
    path = Path(req.file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(req.content, encoding="utf-8")
        return {"success": True, "bytes_written": len(req.content.encode("utf-8")), "error": None}
    except Exception as e:
        return {"success": False, "bytes_written": 0, "error": str(e)}


@app.post("/api/file/edit")
async def edit_file(req: EditFileRequest):
    path = Path(req.file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {req.file_path}"}

    try:
        content = path.read_text(encoding="utf-8")
        if req.old_string not in content:
            return {"success": False, "error": "old_string not found in file"}

        count = content.count(req.old_string)
        if count > 1:
            return {
                "success": False,
                "error": f"old_string found {count} times — must be unique. Provide more context.",
            }

        new_content = content.replace(req.old_string, req.new_string, 1)
        path.write_text(new_content, encoding="utf-8")
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/file/list")
async def list_files(req: ListFilesRequest):
    directory = Path(req.directory)
    if not directory.exists():
        return {"error": f"Directory not found: {req.directory}", "files": []}

    try:
        matches = sorted(directory.glob(req.pattern))
        entries = []
        for p in matches[:500]:  # cap at 500 entries
            stat = p.stat()
            entries.append({
                "name": p.name,
                "path": str(p),
                "is_dir": p.is_dir(),
                "size": stat.st_size if p.is_file() else None,
            })
        return {"files": entries, "total": len(matches), "error": None}
    except Exception as e:
        return {"error": str(e), "files": []}


ALLOWED_MANAGERS = {"pixi", "npm", "pip", "conda"}


@app.post("/api/package/install")
async def install_package(req: InstallPackageRequest):
    if req.manager not in ALLOWED_MANAGERS:
        return {
            "success": False,
            "error": f"Unsupported manager: {req.manager}. Use: {ALLOWED_MANAGERS}",
            "output": "",
        }

    commands = {
        "pixi": f"pixi global install {req.packages}",
        "npm": f"npm install -g {req.packages}",
        "pip": f"pip install {req.packages}",
        "conda": f"pixi global install {req.packages}",  # route conda through pixi
    }

    cmd = commands[req.manager]
    try:
        result = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HOME": "/home/pixi"},
        )
        output = result.stdout + result.stderr
        if len(output) > MAX_OUTPUT_BYTES:
            output = output[:MAX_OUTPUT_BYTES] + "\n... [truncated]"

        return {
            "success": result.returncode == 0,
            "output": output,
            "exit_code": result.returncode,
            "error": None if result.returncode == 0 else "Install failed",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "exit_code": -1, "error": "Install timed out after 120s"}
    except Exception as e:
        return {"success": False, "output": "", "exit_code": -1, "error": str(e)}
