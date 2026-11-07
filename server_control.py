"""Fixed local shutdown actions; never accepts commands, paths or model IDs from HTTP."""
import asyncio
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

STATE = Path(__file__).resolve().parent / ".runtime"
GATEWAY_STOP = STATE / "gateway-stop"
STOP_STATUS = STATE / "server-stop-status.json"
STOP_REQUEST = STATE / "server-stop-request"
SERVICE_PORTS = (8081, 8787, 1234)


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def read_status():
    try:
        return json.loads(STOP_STATUS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


@contextmanager
def lifecycle_lock(timeout=300):
    """Same Windows mutex as start.ps1; serialize double clicks and startup."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    root = str(Path(__file__).resolve().parent).lower()
    suffix = hashlib.sha256(root.encode("utf-8")).hexdigest()[:12].upper()
    handle = kernel.CreateMutexW(None, False, "Local\\LocalAIserverStartup-" + suffix)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, int(timeout * 1000))
        acquired = result in (0, 0x80)  # normal / abandoned owner
        if not acquired:
            raise RuntimeError("Startup or another shutdown is still running. Try again when it finishes.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def write_status(phase, errors=None):
    STATE.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATE, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"phase": phase, "updated_at": time.time(), "errors": errors or []}, stream)
        os.replace(temporary, STOP_STATUS)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_start():
    status = read_status()
    if status.get("phase") in ("requested", "stopping") and (
        time.time() - status.get("updated_at", 0) < 300
        or (STOP_REQUEST.exists() and any(port_open(p) for p in SERVICE_PORTS))
    ):
        raise RuntimeError("Server shutdown is still in progress. Wait a moment before starting again.")
    STOP_REQUEST.unlink(missing_ok=True)
    GATEWAY_STOP.unlink(missing_ok=True)


async def watch_stop_request(queue, app):
    """Private local file signal, never an unauthenticated HTTP endpoint."""
    while not STOP_REQUEST.exists():
        await asyncio.sleep(0.25)
    callback = getattr(app.state, "request_shutdown", None)
    if callback is None:
        return
    write_status("stopping")
    app.state.shutdown_requested = True
    # Close admission, retain partial answers, and wait for file-thread cleanup.
    await queue.shutdown()
    callback()


def request_local_stop(timeout=180):
    """Called by the PC owner, with no browser, session or supplied commands."""
    with lifecycle_lock():
        status = read_status()
        if status.get("phase") == "stopped" and not any(port_open(p) for p in SERVICE_PORTS):
            return
        started = time.time()
        already_stopping = (status.get("phase") == "stopping"
                            and started - status.get("updated_at", 0) < 300)
        backend_running = port_open(8081)
        if not already_stopping:
            write_status("requested")
        STATE.mkdir(parents=True, exist_ok=True)
        STOP_REQUEST.touch()
        if not backend_running and not already_stopping:
            # Recover a partial installation without killing arbitrary processes.
            stop_local_services()
        deadline = time.monotonic() + timeout
        while True:
            status = read_status()
            if status.get("updated_at", 0) >= started:
                if status.get("phase") == "failed":
                    raise RuntimeError("; ".join(status.get("errors", [])))
                if status.get("phase") == "stopped" and not any(port_open(p) for p in SERVICE_PORTS):
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError("Shutdown has not completed. Check .runtime/server-stop-status.json; no process was force-killed.")
            time.sleep(0.25)


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


def main():
    if sys.argv[1:] != ["stop"]:
        print("Usage: server_control.py stop", file=sys.stderr)
        return 2
    print("서버를 종료합니다. 진행·대기 요청을 중지하고 정리가 끝날 때까지 기다립니다.", flush=True)
    try:
        request_local_stop()
    except (OSError, ValueError, RuntimeError) as error:
        print("종료 확인 실패: " + str(error), file=sys.stderr)
        return 1
    print("서버 종료 완료. 다시 사용하려면 실행.cmd를 실행하세요.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
