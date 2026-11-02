"""Bounded, non-executable Windows file operations for the local agent.

This is an application capability boundary, not an OS process sandbox. No API in
this module runs commands, loads code, follows links, or serves arbitrary paths.
Windows handles keep ancestor directories from being renamed during operations.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re


class SandboxViolation(PermissionError):
    """The requested operation is outside the permitted file capability."""


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
_PROTECTED = {".git", ".cptr", ".uploads", ".system"}
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)

if os.name == "nt":
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel.ReadFile.restype = wintypes.BOOL
    _kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel.WriteFile.restype = wintypes.BOOL
    _kernel.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
    _kernel.SetFilePointerEx.restype = wintypes.BOOL
    _kernel.SetEndOfFile.argtypes = [wintypes.HANDLE]
    _kernel.SetEndOfFile.restype = wintypes.BOOL
    _kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _kernel.FlushFileBuffers.restype = wintypes.BOOL
    _kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _kernel.CreateDirectoryW.restype = wintypes.BOOL


def _win_error():
    return ctypes.WinError(ctypes.get_last_error())


def _native(path: PureWindowsPath) -> str:
    return "\\\\?\\" + str(path)


def _component_ok(part: str) -> None:
    if (not part or part in {".", ".."} or part[-1:] in {".", " "}
            or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)
            or _RESERVED.match(part) or part.casefold() in _PROTECTED
            or len(part) > 255):
        raise SandboxViolation("허용되지 않는 파일 또는 폴더 이름입니다.")


class SafeWorkspace:
    """File-only capabilities rooted at one existing, non-reparse directory.

    All path inputs are relative to root or absolute paths strictly inside root.
    Root deletion/mutation and private implementation directories are unavailable.
    Files with multiple hard links and any Windows reparse points are rejected.
    """

    def __init__(self, root: Path):
        if os.name != "nt":
            raise RuntimeError("This boundary requires Windows file-handle semantics.")
        # Do not resolve: resolve() would silently accept a junction as the root.
        self.root = PureWindowsPath(os.path.abspath(str(root)))
        if not self.root.is_absolute() or str(self.root).startswith("\\"):
            raise SandboxViolation("Sandbox root must be a local drive directory.")
        if len(self.root.parts) < 2:
            raise SandboxViolation("A whole drive cannot be an agent workspace.")
        for component in self.root.parts[1:]:
            _component_ok(component)
        with self._directory_handles(self.root):
            pass

    def _path(self, value: str | os.PathLike = ".") -> PureWindowsPath:
        if not isinstance(value, (str, os.PathLike)):
            raise SandboxViolation("경로는 문자열이어야 합니다.")
        raw = os.fspath(value)
        if not isinstance(raw, str) or len(raw) > 8192 or "\x00" in raw:
            raise SandboxViolation("허용되지 않는 경로입니다.")
        raw = raw.replace("/", "\\")
        if raw in {"", "."}:
            return self.root
        if raw.startswith("\\"):
            raise SandboxViolation("네트워크 및 장치 경로는 사용할 수 없습니다.")
        absolute = bool(re.match(r"^[A-Za-z]:\\", raw))
        if not absolute and ":" in raw:
            raise SandboxViolation("드라이브 상대 경로와 대체 데이터 스트림은 사용할 수 없습니다.")
        chunks = raw[3:].split("\\") if absolute else raw.split("\\")
        # A trailing separator is harmless; interior empty/dot components are not.
        if chunks[-1:] == [""]:
            chunks = chunks[:-1]
        for component in chunks:
            _component_ok(component)
        path = PureWindowsPath(raw) if absolute else self.root.joinpath(*chunks)
        root_parts = tuple(part.casefold() for part in self.root.parts)
        path_parts = tuple(part.casefold() for part in path.parts)
        if path_parts[:len(root_parts)] != root_parts:
            raise SandboxViolation("에이전트 작업 폴더 밖에는 접근할 수 없습니다.")
        return path

    def _relative(self, path: PureWindowsPath) -> str:
        return str(path.relative_to(self.root)).replace("\\", "/")

    @staticmethod
    def _open(path, access, share, disposition):
        handle = _kernel.CreateFileW(_native(path), access, share, None, disposition,
                                     0x00200000 | 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise _win_error()
        return handle

    @staticmethod
    def _check_handle(handle, path, directory: bool):
        info = _FileInfo()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise _win_error()
        if info.attributes & 0x400:
            raise SandboxViolation("심볼릭 링크, 정션 및 재분석 지점은 사용할 수 없습니다.")
        if bool(info.attributes & 0x10) != directory:
            if directory:
                raise NotADirectoryError(str(path))
            raise IsADirectoryError(str(path))
        if not directory and info.links != 1:
            raise SandboxViolation("하드 링크 파일에는 접근할 수 없습니다.")
        buffer = ctypes.create_unicode_buffer(32768)
        length = _kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise SandboxViolation("실제 파일 위치를 확인할 수 없습니다.")
        actual = buffer.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if ntpath.normcase(actual) != ntpath.normcase(str(path)):
            raise SandboxViolation("실제 파일 위치가 허용된 경로와 다릅니다.")
        return info

    @contextmanager
    def _directory_handles(self, path: PureWindowsPath):
        """Lock every ancestor against deletion/rename and reject all reparse hops."""
        handles = []
        try:
            components = path.parts
            current = PureWindowsPath(components[0])
            for index in range(len(components)):
                if index:
                    current /= components[index]
                handle = self._open(current, 0x81, 0x1 | 0x2, 3)
                handles.append(handle)
                self._check_handle(handle, current, directory=True)
            yield handles
        finally:
            for handle in reversed(handles):
                _kernel.CloseHandle(handle)

    @contextmanager
    def _file_handle(self, path: PureWindowsPath, writing=False, create_only=False):
        if path == self.root:
            raise SandboxViolation("작업 폴더 자체는 파일로 변경할 수 없습니다.")
        with self._directory_handles(path.parent):
            handle = self._open(path, (0x40000000 | 0x80) if writing else 0x80000000,
                                0 if writing else 0x1,
                                (1 if create_only else 4) if writing else 3)
            try:
                info = self._check_handle(handle, path, directory=False)
                yield handle, info
            finally:
                _kernel.CloseHandle(handle)

    def list_dir(self, path=".") -> dict:
        target = self._path(path)
        entries = []
        with self._directory_handles(target):
            with os.scandir(str(target)) as scan:
                for index, entry in enumerate(scan):
                    if index >= 5000:
                        break
                    try:
                        _component_ok(entry.name)
                        child = target / entry.name
                        attrs = entry.stat(follow_symlinks=False)
                        if getattr(attrs, "st_file_attributes", 0) & 0x400:
                            continue
                        is_dir = bool(getattr(attrs, "st_file_attributes", 0) & 0x10)
                        handle = self._open(child, 0x80, 0x1 | 0x2, 3)
                        try:
                            info = self._check_handle(handle, child, directory=is_dir)
                        finally:
                            _kernel.CloseHandle(handle)
                        entries.append({"name": entry.name, "path": self._relative(child),
                                        "type": "directory" if is_dir else "file",
                                        "size": 0 if is_dir else (info.size_high << 32) | info.size_low})
                    except (OSError, ValueError):
                        continue
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
        return {"path": self._relative(target), "entries": entries}

    def read_bytes(self, path, max_bytes=MAX_FILE_BYTES) -> bytes:
        max_bytes = max(0, min(int(max_bytes), MAX_FILE_BYTES))
        target = self._path(path)
        with self._file_handle(target) as (handle, info):
            if ((info.size_high << 32) | info.size_low) > max_bytes:
                raise ValueError("파일이 읽기 제한을 초과합니다.")
            content = bytearray()
            while True:
                size = min(128 * 1024, max_bytes + 1 - len(content))
                buffer = ctypes.create_string_buffer(size)
                count = wintypes.DWORD()
                if not _kernel.ReadFile(handle, buffer, size, ctypes.byref(count), None):
                    raise _win_error()
                if not count.value:
                    break
                content.extend(buffer.raw[:count.value])
                if len(content) > max_bytes:
                    raise ValueError("파일이 읽기 제한을 초과합니다.")
            return bytes(content)

    def read_text(self, path, max_bytes=MAX_TEXT_BYTES) -> str:
        content = self.read_bytes(path, max_bytes)
        if b"\x00" in content:
            raise ValueError("바이너리 파일은 텍스트 편집기로 열 수 없습니다.")
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("UTF-8 텍스트 파일만 열 수 있습니다.") from error

    def write_bytes(self, path, data: bytes, *, create_only=False) -> dict:
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise ValueError("파일은 10 MB 이하여야 합니다.")
        target = self._path(path)
        with self._file_handle(target, writing=True, create_only=create_only) as (handle, _):
            # OPEN_ALWAYS above never truncates an unchecked existing link.
            if not _kernel.SetFilePointerEx(handle, 0, None, 0) or not _kernel.SetEndOfFile(handle):
                raise _win_error()
            offset = 0
            while offset < len(data):
                chunk = data[offset:offset + 128 * 1024]
                buffer = ctypes.create_string_buffer(chunk)
                written = wintypes.DWORD()
                if not _kernel.WriteFile(handle, buffer, len(chunk), ctypes.byref(written), None):
                    raise _win_error()
                if not written.value:
                    raise OSError("파일 쓰기에 실패했습니다.")
                offset += written.value
            if not _kernel.FlushFileBuffers(handle):
                raise _win_error()
        return {"path": self._relative(target), "size": len(data)}

    def write_text(self, path, content: str, *, create_only=False) -> dict:
        if not isinstance(content, str):
            raise ValueError("내용은 문자열이어야 합니다.")
        return self.write_bytes(path, content.encode("utf-8"), create_only=create_only)

    def mkdir(self, path) -> dict:
        target = self._path(path)
        if target == self.root:
            raise SandboxViolation("작업 폴더 자체는 변경할 수 없습니다.")
        # Hold all existing/new ancestors until the entire operation completes.
        with self._directory_handles(self.root):
            handles = []
            current = self.root
            try:
                for component in target.relative_to(self.root).parts:
                    current /= component
                    if not _kernel.CreateDirectoryW(_native(current), None):
                        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
                            raise _win_error()
                    handle = self._open(current, 0x81, 0x1 | 0x2, 3)
                    handles.append(handle)
                    self._check_handle(handle, current, directory=True)
            finally:
                for handle in reversed(handles):
                    _kernel.CloseHandle(handle)
        return {"path": self._relative(target)}

    def metadata(self, path) -> dict:
        target = self._path(path)
        with self._file_handle(target) as (_, info):
            return {"path": self._relative(target), "name": target.name,
                    "size": (info.size_high << 32) | info.size_low}

    def search(self, query: str, path=".", limit=50) -> list[dict]:
        if not isinstance(query, str) or not 1 <= len(query) <= 256:
            raise ValueError("검색어는 1~256자여야 합니다.")
        limit = max(1, min(int(limit), 100))
        self._path(path)
        pending = [(path, 0)]
        found = []
        visited = 0
        text_bytes = 0
        needle = query.casefold()
        while pending and visited < 2000 and text_bytes < 20 * MAX_TEXT_BYTES:
            directory, depth = pending.pop()
            try:
                entries = self.list_dir(directory)["entries"]
            except (OSError, ValueError):
                continue
            for entry in entries:
                visited += 1
                if visited > 2000:
                    break
                if entry["type"] == "directory":
                    if depth < 20:
                        pending.append((entry["path"], depth + 1))
                    continue
                try:
                    content = self.read_text(entry["path"])
                except (OSError, ValueError):
                    continue
                text_bytes += entry["size"]
                for number, line in enumerate(content.splitlines(), 1):
                    if needle in line.casefold():
                        found.append({"path": entry["path"], "line": number, "text": line[:500]})
                        if len(found) >= limit:
                            return found
        return found
