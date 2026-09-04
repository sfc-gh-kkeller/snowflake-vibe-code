#!/usr/bin/env python3
"""
Workspace backup daemon for SPCS devcontainers.

Backs up the workspace to a stage-backed volume mounted at /backup.
The stage volume persists across service drops/recreates, so workspace
content survives even when the block volume (home) is destroyed.

On startup: restores workspace from backup if workspace is empty.
On SIGTERM: performs one final backup before exit.
Periodically: overwrites the backup tar.gz with current workspace state.
"""
import os
import shutil
import signal
import subprocess
import sys
import time

PROJECT_ID = os.environ.get("PROJECT_ID", "unknown")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/home/pixi/workspace")
BACKUP_INTERVAL = int(os.environ.get("BACKUP_INTERVAL_SECONDS", "300"))
BACKUP_DIR = "/backup"
BACKUP_FILE = os.path.join(BACKUP_DIR, "workspace_backup.tar.gz")
BACKUP_TMP = "/tmp/workspace_backup.tar.gz"

# Directories to exclude from backup (large, regenerable)
EXCLUDES = [
    "node_modules",
    ".next",
    ".pixi",
    "__pycache__",
    ".git/objects",
    "target",
    "dist",
    ".cache",
    "venv",
    ".venv",
]

running = True


def signal_handler(signum, frame):
    global running
    print(f"[backup] Received signal {signum}, performing final backup...", flush=True)
    do_backup()
    running = False
    sys.exit(0)


def workspace_has_content():
    """Check if workspace has real content (ignoring lost+found from block storage)."""
    if not os.path.exists(WORKSPACE_DIR):
        return False
    contents = [f for f in os.listdir(WORKSPACE_DIR) if f != "lost+found"]
    return len(contents) > 0


def do_backup():
    """Tar workspace and write to the stage-backed volume."""
    if not workspace_has_content():
        print("[backup] Workspace empty, skipping.", flush=True)
        return

    if not os.path.isdir(BACKUP_DIR):
        print(f"[backup] Backup dir {BACKUP_DIR} not mounted, skipping.", flush=True)
        return

    exclude_args = []
    for ex in EXCLUDES:
        exclude_args.extend(["--exclude", ex])

    try:
        # Tar to /tmp first, then move to stage volume (atomic-ish write)
        cmd = ["tar", "-czf", BACKUP_TMP] + exclude_args + ["-C", WORKSPACE_DIR, "."]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"[backup] tar failed: {result.stderr[:200]}", flush=True)
            return

        size = os.path.getsize(BACKUP_TMP)
        if size < 100:
            print("[backup] Backup too small, skipping.", flush=True)
            return

        # Move to stage-backed volume (overwrites previous backup).
        # shutil.move handles cross-device moves (/tmp -> /backup are different mounts);
        # os.replace would raise Errno 18 Invalid cross-device link.
        shutil.move(BACKUP_TMP, BACKUP_FILE)
        print(f"[backup] Saved {size} bytes to {BACKUP_FILE}", flush=True)

    except Exception as e:
        print(f"[backup] Error: {e}", flush=True)
    finally:
        try:
            os.remove(BACKUP_TMP)
        except OSError:
            pass


def do_restore():
    """Restore workspace from stage-backed volume if workspace is empty."""
    if workspace_has_content():
        return False

    if not os.path.exists(BACKUP_FILE):
        print("[backup] No backup found, starting fresh.", flush=True)
        return False

    size = os.path.getsize(BACKUP_FILE)
    if size < 100:
        print("[backup] Backup file too small, ignoring.", flush=True)
        return False

    print(f"[backup] Workspace empty, restoring from {BACKUP_FILE} ({size} bytes)...", flush=True)

    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        result = subprocess.run(
            ["tar", "-xzf", BACKUP_FILE, "-C", WORKSPACE_DIR],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"[backup] Restore tar failed: {result.stderr[:200]}", flush=True)
            return False

        print("[backup] Restored workspace from backup.", flush=True)
        return True
    except Exception as e:
        print(f"[backup] Restore failed: {e}", flush=True)
        return False


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # On startup, attempt restore if needed
    do_restore()

    print(f"[backup] Daemon started. Interval={BACKUP_INTERVAL}s, project={PROJECT_ID}", flush=True)

    while running:
        time.sleep(BACKUP_INTERVAL)
        if running:
            do_backup()
