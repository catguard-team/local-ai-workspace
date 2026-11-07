"""Fixed local shutdown actions; never accepts commands, paths or model IDs from HTTP."""
import json
import os
from pathlib import Path
import subprocess
import time

STATE = Path(__file__).resolve().parent / ".runtime"
GATEWAY_STOP = STATE / "gateway-stop"
STOP_STATUS = STATE / "server-stop-status.json"


def write_status(phase, errors=None):
    STATE.mkdir(parents=True, exist_ok=True)
    temporary = STOP_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps({"phase": phase, "updated_at": time.time(),
                                     "errors": errors or []}), encoding="utf-8")
    os.replace(temporary, STOP_STATUS)


def prepare_start():
    if STOP_STATUS.exists():
        status = json.loads(STOP_STATUS.read_text(encoding="utf-8"))
        if status.get("phase") == "stopping" and time.time() - status.get("updated_at", 0) < 120:
            raise RuntimeError("Server shutdown is still in progress. Wait a moment before starting again.")
    GATEWAY_STOP.unlink(missing_ok=True)


def stop_local_services():
    """Called only after Uvicorn and queue cleanup have finished."""
    write_status("stopping")
    GATEWAY_STOP.touch()
    cli = Path.home() / ".lmstudio" / "bin" / "lms.exe"
    errors = []

    def run(arguments):
        result = subprocess.run([str(cli), *arguments], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("LM Studio command failed")
        return result.stdout

    try:
        loaded = json.loads(run(["ps", "--json"]))
        if not isinstance(loaded, list) or any(not isinstance(model, dict) for model in loaded):
            raise ValueError("Invalid LM Studio model list")
        if any(model.get("identifier") == "qwen3.8-27b" for model in loaded):
            run(["unload", "qwen3.8-27b"])
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired):
        errors.append("Could not verify/unload qwen3.8-27b; check LM Studio.")
    try:
        run(["server", "stop"])
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        errors.append("Could not stop LM Studio API; check LM Studio.")
    write_status("failed" if errors else "stopped", errors)
