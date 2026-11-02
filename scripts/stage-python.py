"""Stage a real CPython 3.12 runtime outside a packaged app's virtual AppData."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "bin" / "python312"


def physical_path(path: Path) -> Path:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Could not resolve the physical Python installation path.")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        count = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 0 < count < len(buffer):
            raise OSError("Could not resolve the physical Python installation path.")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    finally:
        kernel.CloseHandle(handle)


def check_python(executable: Path) -> None:
    code = "import sys,struct,ssl,sqlite3,venv,ensurepip; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8"
    result = subprocess.run([str(executable), "-I", "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("A complete working CPython 3.12 x64 installation with venv/ensurepip is required.")


def stage() -> None:
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Use a complete CPython 3.12 x64 Windows installation.")
    root_physical = physical_path(ROOT)
    if str(root_physical).casefold() != str(ROOT).casefold() or "\\appdata\\local\\packages\\" in str(root_physical).casefold():
        raise RuntimeError("Place this repository in a real directory outside packaged-app AppData, such as C:\\Projects\\local-ai-server.")
    if RUNTIME.exists():
        if RUNTIME.is_symlink() or RUNTIME.stat().st_file_attributes & 0x400:
            raise RuntimeError("bin/python312 must not be a filesystem link.")
        check_python(RUNTIME / "python.exe")
        print("Workspace Python runtime is ready.")
        return
    source = physical_path(Path(sys._base_executable)).parent
    check_python(source / "python.exe")
    RUNTIME.parent.mkdir(exist_ok=True)
    if RUNTIME.parent.is_symlink() or RUNTIME.parent.stat().st_file_attributes & 0x400:
        raise RuntimeError("bin must not be a filesystem link.")
    if source == RUNTIME.parent or source in RUNTIME.parents or ROOT in source.parents:
        raise RuntimeError("Use a complete external base Python installation as the staging source.")
    staging = RUNTIME.parent / (".python312-setup-" + uuid.uuid4().hex)

    def exclude(directory: str, names: list[str]) -> list[str]:
        ignored = {"site-packages", "__pycache__", ".venv", "Scripts"}.intersection(names)
        for name in set(names) - ignored:
            path = Path(directory) / name
            if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
                raise RuntimeError("The Python installation contains a filesystem link; choose a standard complete installation.")
        return list(ignored)

    shutil.copytree(source, staging, ignore=exclude)
    check_python(staging / "python.exe")
    staging.rename(RUNTIME)
    print("Copied standalone CPython 3.12 into bin/python312.")


def check_venv() -> None:
    if Path(sys.prefix) != ROOT / ".venv" or Path(sys.base_prefix) != RUNTIME:
        raise RuntimeError("The existing .venv does not use this repository's bin/python312 runtime. Use a fresh checkout for bootstrap.")
    import ssl
    import sqlite3
    print(json.dumps({"python": str(Path(sys.executable)), "workspace_runtime": True}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-venv", action="store_true")
    args = parser.parse_args()
    try:
        check_venv() if args.check_venv else stage()
        return 0
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
