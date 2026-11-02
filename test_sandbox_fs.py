"""Security regression tests; all fixtures remain under .runtime/sandbox-fs-* ."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from sandbox_fs import SafeWorkspace, SandboxViolation


@unittest.skipUnless(os.name == "nt", "Windows native handle boundary")
class SafeWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.state = (Path(__file__).parent / ".runtime").resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="sandbox-fs-", dir=self.state)
        self.base = Path(self.temp.name)
        assert self.base.resolve().is_relative_to(self.state)
        assert self.base.name.startswith("sandbox-fs-")
        self.root = self.base / "agent"
        self.root.mkdir()
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside unchanged", encoding="utf-8")
        self.fs = SafeWorkspace(self.root)
        self.junctions = []

    def tearDown(self):
        # Remove junction itself, never recursively traverse its target.
        for junction in self.junctions:
            if junction.exists():
                os.rmdir(junction)
        assert self.base.resolve().is_relative_to(self.state)
        self.temp.cleanup()

    def test_file_operations_and_search(self):
        self.fs.mkdir("project/src")
        value = "안녕하세요\nSandbox test\n"
        self.assertEqual(self.fs.write_text("project/src/test.txt", value)["size"], len(value.encode()))
        self.assertEqual(self.fs.read_text("project/src/test.txt"), value)
        self.assertEqual(self.fs.read_text(str(self.root / "project/src/test.txt")), value)
        self.assertEqual(self.fs.list_dir("project/src")["entries"][0]["path"], "project/src/test.txt")
        self.assertEqual(self.fs.search("SANDBOX"), [{"path": "project/src/test.txt", "line": 2, "text": "Sandbox test"}])
        self.fs.write_text("project/src/test.txt", "short")
        self.assertEqual(self.fs.read_bytes("project/src/test.txt"), b"short")
        self.assertEqual(self.fs.metadata("project/src/test.txt")["size"], 5)

    def test_outside_and_special_paths_denied(self):
        paths = [
            "../outside/secret.txt", str(self.outside / "secret.txt"),
            str(self.root) + "-sibling/file.txt", "C:\\Windows\\win.ini",
            "C:relative.txt", "\\Windows\\win.ini", "\\\\server\\share\\x",
            "\\\\?\\C:\\Windows\\win.ini", "\\\\.\\GLOBALROOT\\x", "file.txt:secret",
            "test/../ok.txt", "test/./ok.txt", "file.txt.", "file.txt ",
            "CON", "NUL.txt", "AUX", "PRN.log", "COM1.txt", "LPT9", "COM¹.txt",
            "CONIN$", "CONOUT$", "bad\x00name", "bad\nname", "*.txt",
            ".git/config", ".cptr/settings.json", ".uploads/anything", ".system/x",
        ]
        for path in paths:
            with self.subTest(path=path):
                with self.assertRaises(SandboxViolation):
                    self.fs.read_bytes(path)
                with self.assertRaises(SandboxViolation):
                    self.fs.write_text(path, "bad")
        self.assertEqual((self.outside / "secret.txt").read_text(), "outside unchanged")

    def test_root_mutation_denied(self):
        with self.assertRaises(SandboxViolation):
            self.fs.write_text(".", "bad")
        with self.assertRaises(SandboxViolation):
            self.fs.mkdir(".")
        self.assertEqual(self.fs.list_dir()["path"], ".")

    def test_create_only_does_not_overwrite(self):
        self.fs.write_text("new.txt", "first", create_only=True)
        with self.assertRaises(FileExistsError):
            self.fs.write_text("new.txt", "second", create_only=True)
        self.assertEqual(self.fs.read_text("new.txt"), "first")

    def test_hard_link_denied_before_truncate(self):
        linked = self.root / "linked.txt"
        linked.hardlink_to(self.outside / "secret.txt")
        with self.assertRaises(SandboxViolation):
            self.fs.read_text("linked.txt")
        with self.assertRaises(SandboxViolation):
            self.fs.write_text("linked.txt", "bad")
        self.assertEqual(self.fs.list_dir()["entries"], [])
        self.assertEqual((self.outside / "secret.txt").read_text(), "outside unchanged")

    def test_junction_denied_and_not_listed_or_searched(self):
        junction = self.root / "escape"
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(self.outside)],
                                capture_output=True, text=True, encoding="mbcs", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.junctions.append(junction)
        for operation in [lambda: self.fs.list_dir("escape"),
                          lambda: self.fs.read_text("escape/secret.txt"),
                          lambda: self.fs.write_text("escape/secret.txt", "bad"),
                          lambda: self.fs.mkdir("escape/created")]:
            with self.assertRaises(SandboxViolation):
                operation()
        self.assertEqual(self.fs.list_dir()["entries"], [])
        self.assertEqual(self.fs.search("outside"), [])
        self.assertFalse((self.outside / "created").exists())
        self.assertEqual((self.outside / "secret.txt").read_text(), "outside unchanged")
        with self.assertRaises(SandboxViolation):
            SafeWorkspace(junction)

    def test_ancestor_rename_is_locked_during_operation(self):
        self.fs.mkdir("project")
        project = self.root / "project"
        with self.fs._directory_handles(self.fs._path("project")):
            with self.assertRaises(PermissionError):
                project.rename(self.root / "renamed")
        project.rename(self.root / "renamed")
        self.assertTrue((self.root / "renamed").is_dir())

    def test_file_limits_and_binary_edit_rejection(self):
        self.fs.write_bytes("data.bin", b"\x00\x01\x02")
        self.assertEqual(self.fs.read_bytes("data.bin"), b"\x00\x01\x02")
        with self.assertRaises(ValueError):
            self.fs.read_text("data.bin")
        with self.assertRaises(ValueError):
            self.fs.read_bytes("data.bin", max_bytes=2)
        with self.assertRaises(ValueError):
            self.fs.write_bytes("huge.bin", b"x" * (10 * 1024 * 1024 + 1))
        self.assertFalse((self.root / "huge.bin").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
