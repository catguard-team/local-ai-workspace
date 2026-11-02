"""Fresh-install account checks using isolated synthetic data only."""
from contextlib import closing
from contextlib import ExitStack
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bcrypt

from account_store import create_user, ensure_schema, list_users, normalize_username, password_bytes, reset_password


SYNTHETIC_PASSWORD = "Synthetic-fixture-password-42!"


class AccountSetupTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parent / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="account-setup-test-", dir=runtime)
        self.database = Path(self.temp.name) / "app.db"
        self.salt = bcrypt.gensalt(rounds=4)
        self.patcher = patch("account_store.bcrypt.gensalt", return_value=self.salt)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_fresh_account_without_legacy_database(self):
        self.assertEqual(create_user(self.database, "  Fixture_A  ", SYNTHETIC_PASSWORD), "fixture_a")
        with closing(sqlite3.connect(self.database)) as con:
            row = con.execute("SELECT username,password FROM users").fetchone()
            self.assertEqual(row[0], "fixture_a")
            self.assertNotEqual(row[1], SYNTHETIC_PASSWORD)
            self.assertTrue(bcrypt.checkpw(SYNTHETIC_PASSWORD.encode(), row[1].encode()))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM chats").fetchone()[0], 0)
        self.assertEqual(list_users(self.database), ["fixture_a"])

    def test_duplicate_does_not_overwrite_credentials(self):
        create_user(self.database, "fixture_a", SYNTHETIC_PASSWORD)
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_user(self.database, "FIXTURE_A", "Different-synthetic-password-99!")
        with closing(sqlite3.connect(self.database)) as con:
            stored = con.execute("SELECT password FROM users").fetchone()[0]
        self.assertTrue(bcrypt.checkpw(SYNTHETIC_PASSWORD.encode(), stored.encode()))

    def test_add_account_preserves_in_progress_chat(self):
        create_user(self.database, "fixture_a", SYNTHETIC_PASSWORD)
        with closing(sqlite3.connect(self.database)) as con, con:
            user_id = con.execute("SELECT id FROM users").fetchone()[0]
            con.execute("INSERT INTO chats(id,user_id,title,created_at,updated_at,request_state,request_message_id) VALUES('chat',?,'fixture',1,1,'running','message')", (user_id,))
            con.execute("INSERT INTO messages(id,chat_id,role,content,created_at) VALUES('message','chat','assistant','partial fixture',1)")
        create_user(self.database, "fixture_b", SYNTHETIC_PASSWORD)
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT request_state,request_message_id FROM chats").fetchone(), ("running", "message"))
            self.assertEqual(con.execute("SELECT content FROM messages").fetchone()[0], "partial fixture")

    def test_password_reset_revokes_only_target_sessions(self):
        for name in ("fixture_a", "fixture_b"):
            create_user(self.database, name, SYNTHETIC_PASSWORD)
        with closing(sqlite3.connect(self.database)) as con, con:
            users = dict(con.execute("SELECT username,id FROM users"))
            con.executemany("INSERT INTO sessions VALUES(?,?,?)", [("token-a", users["fixture_a"], 9999999999), ("token-b", users["fixture_b"], 9999999999)])
        reset_password(self.database, "fixture_a", "Changed-synthetic-password-42!")
        with closing(sqlite3.connect(self.database)) as con:
            self.assertEqual(con.execute("SELECT token FROM sessions").fetchall(), [("token-b",)])
            stored = con.execute("SELECT password FROM users WHERE username='fixture_a'").fetchone()[0]
        self.assertFalse(bcrypt.checkpw(SYNTHETIC_PASSWORD.encode(), stored.encode()))
        self.assertTrue(bcrypt.checkpw(b"Changed-synthetic-password-42!", stored.encode()))

    def test_missing_reset_account_never_creates_one(self):
        create_user(self.database, "fixture_a", SYNTHETIC_PASSWORD)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            reset_password(self.database, "fixture_b", SYNTHETIC_PASSWORD)
        self.assertEqual(list_users(self.database), ["fixture_a"])

    def test_password_and_username_boundaries(self):
        for invalid in ("short", "a" * 73, "한" * 25):
            with self.assertRaises(ValueError):
                password_bytes(invalid)
        for invalid in ("ab", "admin@example.test", "../name", "space name", "-starts-with-hyphen"):
            with self.assertRaises(ValueError):
                normalize_username(invalid)
        self.assertEqual(len(password_bytes("한" * 24)), 72)

    def test_schema_migration_is_idempotent_and_preserves_history(self):
        with closing(sqlite3.connect(self.database)) as con, con:
            con.executescript("CREATE TABLE chats(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,created_at REAL,updated_at REAL,error TEXT); INSERT INTO chats VALUES('old','owner','history',1,2,NULL);")
            ensure_schema(con)
            ensure_schema(con)
            self.assertEqual(con.execute("SELECT title,mode,request_state FROM chats").fetchone(), ("history", "chat", "idle"))

    def test_password_prompt_rejects_redirected_input(self):
        from setup_accounts import prompt_password
        with patch("setup_accounts.sys.stdin.isatty", return_value=False), patch("setup_accounts.getpass.getpass") as getpass:
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                prompt_password()
            getpass.assert_not_called()

    def service_fixture(self):
        # The module validates its workspace on import. This is the public
        # checkout's ignored empty workspace, never the live server directory.
        (Path(__file__).resolve().parent / "agent").mkdir(exist_ok=True)
        import sandbox_agent as service
        stack = ExitStack()
        stack.enter_context(patch.object(service, "STATE", self.database.parent))
        stack.enter_context(patch.object(service, "DB", self.database))
        stack.enter_context(patch.object(service, "UPLOADS", self.database.parent / "uploads"))
        stack.enter_context(patch.object(service, "load_vpn_config", return_value=None))
        stack.enter_context(patch.object(service, "load_vpn_public_config", return_value=None))
        stack.enter_context(patch.object(service, "public_url", return_value="http://127.0.0.1:8787"))
        return service, stack

    def test_fresh_server_fails_closed_without_accounts(self):
        service, fixture = self.service_fixture()
        with fixture, self.assertRaisesRegex(RuntimeError, "setup_accounts.py create"):
            service.initialize()

    def test_fresh_server_logs_in_without_legacy_database(self):
        from fastapi.testclient import TestClient
        create_user(self.database, "fixture_a", SYNTHETIC_PASSWORD)
        service, fixture = self.service_fixture()
        with fixture, TestClient(service.app, base_url="http://127.0.0.1:8787") as client:
            self.assertEqual(client.get("/api/me").status_code, 401)
            headers = {"X-Sandbox-Request": "1", "Origin": "http://127.0.0.1:8787"}
            response = client.post("/api/login", json={"username": "fixture_a", "password": SYNTHETIC_PASSWORD}, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/me").json()["username"], "fixture_a")
            self.assertEqual(client.get("/api/chats").json(), [])
            self.assertEqual(client.post("/api/logout", headers=headers).status_code, 200)


if __name__ == "__main__":
    unittest.main()
