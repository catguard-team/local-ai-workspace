"""API boundary regression tests; use temporary state and synthetic accounts only."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import bcrypt
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

import sandbox_agent as service
from sandbox_fs import SafeWorkspace


HEADERS = {"x-sandbox-request": "1"}
PASSWORD = "Synthetic-test-password-42!"
REAL_RUN_AGENT = service.run_agent


async def no_inference(chat_id, assistant_id, user):
    service.save_answer(assistant_id, "Synthetic test response")


class SandboxApiTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parent / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="sandbox-api-test-", dir=runtime)
        self.root = Path(self.temp.name)
        state = self.root / "state"
        state.mkdir()
        agent = self.root / "agent"
        agent.mkdir()
        database = state / "app.db"
        password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
        with sqlite3.connect(database) as con:
            con.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO metadata VALUES('cptr_imported','test-fixture');
                INSERT INTO metadata VALUES('webui_imported','test-fixture');
            """)
            con.executemany("INSERT INTO users VALUES(?,?,?)", [
                ("fixture-a", "test_a", password_hash),
                ("fixture-b", "test_b", password_hash),
            ])
        self.patchers = [
            patch.object(service, "STATE", state),
            patch.object(service, "DB", database),
            patch.object(service, "UPLOADS", state / "uploads"),
            patch.object(service, "ROOT", agent),
            patch.object(service, "workspace", SafeWorkspace(agent)),
            patch.object(service, "jobs", {}),
            patch.object(service, "login_attempts", defaultdict(deque)),
            patch.object(service, "load_vpn_config", return_value=None),
            patch.object(service, "load_vpn_public_config", return_value=None),
            patch.object(service, "run_agent", no_inference),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(service.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.other = TestClient(service.app, base_url="http://127.0.0.1")

    def tearDown(self):
        self.other.close()
        self.client.__exit__(None, None, None)
        for patcher in reversed(self.patchers):
            patcher.stop()
        # sqlite3 context managers commit but do not close; release GC cycles.
        import gc
        gc.collect()
        self.temp.cleanup()

    def login(self, client=None, username="test_a"):
        client = client or self.client
        response = client.post("/api/login", json={"username": username, "password": PASSWORD}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def new_chat(self, client=None):
        response = (client or self.client).post("/api/chats", json={"title": "Fixture chat"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def upload_text(self, client=None):
        response = (client or self.client).post("/api/uploads", files={"file": ("note.txt", b"safe test text", "text/plain")}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_authentication_and_logout(self):
        for path in ("/api/me", "/api/chats", "/api/queue", "/api/files", "/api/file?path=test.txt", "/api/download?path=test.txt"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        bad = self.client.post("/api/login", json={"username": "test_a", "password": "incorrect"}, headers=HEADERS)
        self.assertEqual(bad.status_code, 401)
        response = self.login()
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", response.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/api/me").json()["username"], "test_a")
        self.assertEqual(self.client.post("/api/logout", headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_mutations_require_same_origin_header(self):
        credentials = {"username": "test_a", "password": PASSWORD}
        self.assertEqual(self.client.post("/api/login", json=credentials).status_code, 403)
        bad_headers = {**HEADERS, "origin": "https://attacker.example"}
        self.assertEqual(self.client.post("/api/login", json=credentials, headers=bad_headers).status_code, 403)
        okay_headers = {**HEADERS, "origin": "http://127.0.0.1"}
        self.assertEqual(self.client.post("/api/login", json=credentials, headers=okay_headers).status_code, 200)

    def test_chat_and_attachment_ownership(self):
        self.login()
        self.login(self.other, "test_b")
        chat_id = self.new_chat()
        file_id = self.upload_text()
        self.assertEqual(self.other.get(f"/api/chats/{chat_id}").status_code, 404)
        self.assertEqual(self.other.post(f"/api/chats/{chat_id}/cancel", headers=HEADERS).status_code, 404)
        self.assertEqual(self.other.post(f"/api/chats/{chat_id}/messages", json={"content": "cross-user"}, headers=HEADERS).status_code, 404)
        self.assertEqual(self.other.get(f"/api/uploads/{file_id}").status_code, 404)
        other_chat = self.new_chat(self.other)
        self.assertEqual(self.other.post(f"/api/chats/{other_chat}/messages", json={"attachments": [file_id]}, headers=HEADERS).status_code, 404)
        self.assertEqual(self.other.get("/api/chats").json()[0]["id"], other_chat)
        result = self.client.post(f"/api/chats/{chat_id}/messages", json={"content": "hello", "attachments": [file_id]}, headers=HEADERS)
        self.assertEqual(result.status_code, 202, result.text)

    def test_unknown_execution_parameters_rejected(self):
        self.login()
        chat_id = self.new_chat()
        for key, value in (("model_id", "agent:codex"), ("workspace", "C:/Windows"), ("params", {"tool_approval_mode": "full"}), ("tools", []), ("files", [{"id": "C:/Windows/win.ini"}])):
            response = self.client.post(f"/api/chats/{chat_id}/messages", json={"content": "test", key: value}, headers=HEADERS)
            self.assertEqual(response.status_code, 422, key)
        response = self.client.post("/api/chats", json={"title": "test", "workspace": "C:/Windows"}, headers=HEADERS)
        self.assertEqual(response.status_code, 422)

    def test_legacy_execution_routes_absent(self):
        self.login()
        for path in ("/api/terminal", "/api/git/commit", "/api/browser", "/api/admin/tools/servers", "/api/admin/agents", "/api/automations", "/v1/chat/completions", "/api/workspace/files/write"):
            response = self.client.post(path, json={"command": "whoami", "cwd": "C:/"}, headers=HEADERS)
            self.assertEqual(response.status_code, 404, path)
        for path in ("/api/terminal/example/ws", "/api/events/ws", "/socket.io/"):
            with self.assertRaises(WebSocketDisconnect, msg=path):
                with self.client.websocket_connect(path):
                    self.fail("legacy websocket unexpectedly accepted")

    def test_file_routes_and_tool_dispatch_stay_in_root(self):
        self.login()
        response = self.client.put("/api/file", json={"path": "example.txt", "content": "before"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/file", params={"path": "example.txt"}).json()["content"], "before")
        self.assertNotIn("error", service.execute_tool("edit_file", {"path": "example.txt", "old": "before", "new": "after"}))
        self.assertEqual(service.workspace.read_text("example.txt"), "after")
        for path in ("../outside.txt", "C:/Windows/win.ini", "C:relative.txt", "//server/share/file", "example.txt:secret", "NUL.txt", ".cptr/config.toml", ".uploads/item"):
            self.assertEqual(self.client.get("/api/file", params={"path": path}).status_code, 403, path)
            self.assertIn("error", service.execute_tool("read_file", {"path": path}), path)
            self.assertIn("error", service.execute_tool("write_file", {"path": path, "content": "denied"}), path)
        for tool in ("run_command", "python", "eval", "browser_evaluate", "mcp_stdio", "delegate_task", "create_artifact"):
            self.assertIn("error", service.execute_tool(tool, {"command": "whoami"}), tool)
        self.assertIn("error", service.execute_tool("read_file", {"path": "example.txt", "workspace": "C:/Windows"}))

    def test_uploads_are_validated_and_not_active_content(self):
        self.login()
        response = self.client.post("/api/uploads", files={"file": ("../../attack.html", b"<script>alert(1)</script>", "text/html")}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        file_id = response.json()["id"]
        self.assertEqual(str(uuid.UUID(file_id)), file_id)
        self.assertEqual(response.json()["name"], "attack.html")
        download = self.client.get(f"/api/uploads/{file_id}")
        self.assertTrue(download.headers["content-type"].startswith("text/plain"))
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertIn("sandbox", download.headers["content-security-policy"])
        for filename, data in (("bad.jpg", b"not an image"), ("binary.bin", b"\x00binary"), ("bad.pdf", b"not a PDF"), ("bad.docx", b"not a ZIP")):
            response = self.client.post("/api/uploads", files={"file": (filename, data)}, headers=HEADERS)
            self.assertEqual(response.status_code, 400, filename)
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
        response = self.client.post("/api/uploads", files={"file": ("image.png", buffer.getvalue(), "image/png")}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mime"], "image/jpeg")
        self.assertEqual(self.client.get("/api/uploads/not-a-uuid").status_code, 400)

    def pdf_fixture(self, encrypted=False):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 100 Td (Security regression sample) Tj ET')
        page[NameObject('/Contents')] = stream
        if encrypted:
            writer.encrypt('synthetic-fixture-only')
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_patched_pdf_upload_extracts_text_and_preserves_download(self):
        self.login()
        data = self.pdf_fixture()
        response = self.client.post('/api/uploads', files={'file': ('fixture.pdf', data, 'application/pdf')}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        file_id = response.json()['id']
        with service.db() as con:
            text = con.execute('SELECT extracted FROM uploads WHERE id=?', (file_id,)).fetchone()[0]
        self.assertIn('Security regression sample', text)
        self.assertEqual(self.client.get('/api/uploads/' + file_id).content, data)

    def test_encrypted_pdf_is_rejected_without_persisting_upload(self):
        self.login()
        response = self.client.post('/api/uploads', files={'file': ('encrypted.pdf', self.pdf_fixture(True), 'application/pdf')}, headers=HEADERS)
        self.assertEqual(response.status_code, 400)
        with service.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM uploads').fetchone()[0], 0)

    def test_truncated_xref_trailer_terminates_with_parse_error(self):
        # Minimal EOF fixture from upstream GHSA-5849-834g-hvw5 / PR #4289.
        # Child timeout prevents an accidental dependency regression hanging CI.
        payload = b'%PDF-1.4\nstartxref\n28\n%%EOF\nxref\n1 0\naaa'
        code = "from io import BytesIO\nimport sys\nfrom pypdf import PdfReader\nfrom pypdf.errors import PdfReadError\ntry:\n PdfReader(BytesIO(sys.stdin.buffer.read()))\nexcept PdfReadError:\n sys.exit(0)\nsys.exit(2)"
        result = subprocess.run([sys.executable, '-I', '-c', code], input=payload, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_declared_oversize_request_is_rejected(self):
        self.login()
        response = self.client.post("/api/chats", content=b"{}", headers={**HEADERS, "content-type": "application/json", "content-length": str(service.MAX_UPLOAD + 65537)})
        self.assertEqual(response.status_code, 413)

    def test_chunked_oversize_request_is_rejected(self):
        self.login()
        def chunks():
            for _ in range(12):
                yield b"x" * (1024 * 1024)
        response = self.client.post("/api/uploads", content=chunks(), headers={**HEADERS, "content-type": "application/octet-stream"})
        self.assertEqual(response.status_code, 413)

    def test_chat_is_default_and_agent_mode_requires_valid_explicit_choice(self):
        self.login()
        chat_id = self.new_chat()
        self.assertEqual(self.client.get(f"/api/chats/{chat_id}").json()["mode"], "chat")
        self.assertEqual(self.client.get("/api/chats").json()[0]["mode"], "chat")
        response = self.client.post("/api/chats", json={"mode": "agent"}, headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mode"], "agent")
        for invalid in ("shell", "AGENT", "", None, True):
            self.assertEqual(self.client.post("/api/chats", json={"mode": invalid}, headers=HEADERS).status_code, 422)
            self.assertEqual(self.client.patch(f"/api/chats/{chat_id}/mode", json={"mode": invalid}, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.post(f"/api/chats/{chat_id}/messages", json={"content": "test", "mode": "agent"}, headers=HEADERS).status_code, 422)

    def test_mode_switch_requires_owner_and_idle_chat(self):
        self.login()
        self.login(self.other, "test_b")
        chat_id = self.new_chat()
        endpoint = f"/api/chats/{chat_id}/mode"
        self.assertEqual(self.other.patch(endpoint, json={"mode": "agent"}, headers=HEADERS).status_code, 404)
        self.assertEqual(self.client.patch(endpoint, json={"mode": "agent"}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get(f"/api/chats/{chat_id}").json()["mode"], "agent")
        running = Mock()
        running.done.return_value = False
        with patch.dict(service.jobs, {chat_id: running}):
            self.assertEqual(self.client.patch(endpoint, json={"mode": "chat"}, headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.patch(endpoint, json={"mode": "chat"}, headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.get(f"/api/chats/{chat_id}").json()["mode"], "chat")

    def runner_fixture(self, mode):
        self.login()
        created = self.client.post("/api/chats", json={"mode": mode}, headers=HEADERS)
        self.assertEqual(created.status_code, 200)
        chat_id = created.json()["id"]
        message_id = str(uuid.uuid4())
        with service.db() as con:
            con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (message_id, chat_id, "assistant", "", "[]", 1.0))
        return chat_id, message_id, {"id": "fixture-a", "username": "test_a"}

    @staticmethod
    def provider_write_call():
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_fixture", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "model-created.txt", "content": "bounded file"})}}]}

    def test_chat_mode_refuses_unsolicited_tool_call_even_if_state_changes(self):
        chat_id, message_id, user = self.runner_fixture("chat")

        async def malicious_response(messages, on_chunk, *, use_tools):
            self.assertFalse(use_tools)
            self.assertEqual(messages[0]["content"], service.CHAT_SYSTEM)
            # A mode snapshot must remain fixed throughout an inference request.
            with service.db() as con:
                con.execute("UPDATE chats SET mode='agent' WHERE id=?", (chat_id,))
            return self.provider_write_call()

        async def exercise():
            await REAL_RUN_AGENT(chat_id, message_id, user)

        with patch.object(service, "model_turn", side_effect=malicious_response) as model, patch.object(service, "execute_tool") as dispatch:
            asyncio.run(exercise())
            dispatch.assert_not_called()
            self.assertEqual(model.call_count, 1)
        self.assertFalse((service.ROOT / "model-created.txt").exists())
        with service.db() as con:
            content = con.execute("SELECT content FROM messages WHERE id=?", (message_id,)).fetchone()[0]
        self.assertIn("채팅 모드", content)

    def test_agent_mode_dispatches_only_the_bounded_tool(self):
        chat_id, message_id, user = self.runner_fixture("agent")
        answers = [self.provider_write_call(), {"role": "assistant", "content": "Complete"}]

        async def exercise():
            await REAL_RUN_AGENT(chat_id, message_id, user)

        with patch.object(service, "model_turn", new=AsyncMock(side_effect=answers)) as model, patch.object(service, "execute_tool", wraps=service.execute_tool) as dispatch:
            asyncio.run(exercise())
            dispatch.assert_called_once_with("write_file", {"path": "model-created.txt", "content": "bounded file"})
            self.assertTrue(all(call.kwargs["use_tools"] for call in model.call_args_list))
        self.assertEqual(service.workspace.read_text("model-created.txt"), "bounded file")

    def test_chat_model_payload_omits_all_tool_parameters(self):
        payloads = []
        closed = []

        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                closed.append(True)

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
                yield 'data: [DONE]'
                raise AssertionError("SSE completion marker must close the stream immediately")

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, method, url, *, json):
                payloads.append((method, url, json))
                return FakeResponse()

        async def on_chunk(content):
            pass

        async def exercise():
            await service.model_turn([{"role": "user", "content": "test"}], on_chunk, use_tools=False)
            await service.model_turn([{"role": "user", "content": "test"}], on_chunk, use_tools=True)

        with patch.object(service.httpx, "AsyncClient", FakeClient):
            asyncio.run(exercise())
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0][:2], ("POST", service.MODEL_URL))
        self.assertNotIn("tools", payloads[0][2])
        self.assertNotIn("tool_choice", payloads[0][2])
        self.assertEqual(payloads[1][2]["tools"], service.TOOLS)
        self.assertEqual(payloads[1][2]["tool_choice"], "auto")
        self.assertEqual(len(closed), 2)

    def wait_idle(self, chat_id, headers=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/chats/{chat_id}", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            if not response.json()["running"]:
                return response.json()
            time.sleep(.01)
        self.fail("Synthetic queue request did not finish")

    def test_shared_account_fifo_metadata_and_user_limit_before_append(self):
        self.login()
        a_cookie = self.client.cookies.get(service.COOKIE)
        first, third, rejected = self.new_chat(), self.new_chat(), self.new_chat()
        self.login(self.other, "test_b")
        second = self.new_chat(self.other)
        b_cookie = self.other.cookies.get(service.COOKIE)
        headers_a = {**HEADERS, "cookie": f"{service.COOKIE}={a_cookie}"}
        headers_b = {**HEADERS, "cookie": f"{service.COOKIE}={b_cookie}"}
        gates = {key: asyncio.Event() for key in (first, second, third)}
        starts = {key: threading.Event() for key in gates}
        order, active = [], []

        async def fake_turn(chat_id, assistant_id, user):
            active.append(chat_id)
            self.assertEqual(len(active), 1)
            order.append(chat_id)
            starts[chat_id].set()
            try:
                await gates[chat_id].wait()
                service.save_answer(assistant_id, "Synthetic queued answer")
            finally:
                active.remove(chat_id)

        with patch.object(service, "run_agent", fake_turn):
            try:
                first_response = self.client.post(f"/api/chats/{first}/messages", json={"content": "first"}, headers=headers_a)
                self.assertEqual(first_response.status_code, 202, first_response.text)
                self.assertEqual(first_response.json()["request_state"], "running")
                self.assertTrue(starts[first].wait(2))
                second_response = self.client.post(f"/api/chats/{second}/messages", json={"content": "second"}, headers=headers_b)
                third_response = self.client.post(f"/api/chats/{third}/messages", json={"content": "third"}, headers=headers_a)
                self.assertEqual(second_response.json()["queue_position"], 1)
                self.assertEqual(third_response.json()["queue_position"], 2)
                self.assertEqual(third_response.json()["request_state"], "queued")
                self.assertTrue(third_response.json()["running"])
                self.assertEqual(self.client.get(f"/api/chats/{second}", headers=headers_a).status_code, 404)
                state = self.client.get("/api/queue", headers=headers_b).json()
                self.assertEqual(set(state), {"server_busy", "queue_waiting", "own_pending", "limits"})
                self.assertEqual((state["queue_waiting"], state["own_pending"]), (2, 1))
                refused = self.client.post(f"/api/chats/{rejected}/messages", json={"content": "must not append"}, headers=headers_a)
                self.assertEqual(refused.status_code, 429, refused.text)
                unchanged = self.client.get(f"/api/chats/{rejected}", headers=headers_a).json()
                self.assertEqual(unchanged["messages"], [])
                self.assertEqual(unchanged["title"], "Fixture chat")
                self.client.portal.call(gates[first].set)
                self.assertTrue(starts[second].wait(2))
                self.assertEqual(self.client.get(f"/api/chats/{third}", headers=headers_a).json()["queue_position"], 1)
                self.client.portal.call(gates[second].set)
                self.assertTrue(starts[third].wait(2))
                self.client.portal.call(gates[third].set)
                self.wait_idle(third, headers_a)
                self.assertEqual(order, [first, second, third])
                self.assertFalse(self.client.get("/api/queue", headers=headers_a).json()["server_busy"])
            finally:
                for gate in gates.values():
                    self.client.portal.call(gate.set)

    def test_global_overload_and_queued_cancel_leave_no_extra_message(self):
        self.login()
        first, cancelled, rejected = self.new_chat(), self.new_chat(), self.new_chat()
        gate = asyncio.Event()
        entered = threading.Event()
        async def fake_turn(chat_id, assistant_id, user):
            entered.set()
            await gate.wait()
        service.inference_queue.limits["max_waiting"] = 1
        service.inference_queue.limits["max_pending_per_user"] = 3
        with patch.object(service, "run_agent", fake_turn):
            try:
                self.assertEqual(self.client.post(f"/api/chats/{first}/messages", json={"content": "active"}, headers=HEADERS).status_code, 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.post(f"/api/chats/{cancelled}/messages", json={"content": "queued"}, headers=HEADERS).json()["queue_position"], 1)
                self.assertEqual(self.client.post(f"/api/chats/{rejected}/messages", json={"content": "full"}, headers=HEADERS).status_code, 429)
                self.assertEqual(self.client.get(f"/api/chats/{rejected}").json()["messages"], [])
                result = self.client.post(f"/api/chats/{cancelled}/cancel", headers=HEADERS).json()
                self.assertEqual(result["request_state"], "idle")
                self.assertFalse(result["running"])
                self.assertEqual(result["queue_waiting"], 0)
                self.assertIn("취소", self.client.get(f"/api/chats/{cancelled}").json()["messages"][-1]["content"])
            finally:
                self.client.portal.call(gate.set)
                self.wait_idle(first)

    def test_deadlines_persist_retry_message_and_free_next_request(self):
        self.login()
        active, expired, next_chat = self.new_chat(), self.new_chat(), self.new_chat()
        started = threading.Event()
        never = asyncio.Event()
        async def fake_turn(chat_id, assistant_id, user):
            if chat_id == active:
                started.set()
                await never.wait()
            service.save_answer(assistant_id, "Finished next")
        queue = service.inference_queue
        queue.limits["max_runtime_seconds"] = .5
        queue.limits["max_wait_seconds"] = .15
        with patch.object(service, "run_agent", fake_turn):
            self.assertEqual(self.client.post(f"/api/chats/{active}/messages", json={"content": "timeout"}, headers=HEADERS).status_code, 202)
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.post(f"/api/chats/{expired}/messages", json={"content": "expire"}, headers=HEADERS).status_code, 202)
            expired_result = self.wait_idle(expired)
            self.assertIn("30분", expired_result["error"])
            queue.limits["max_wait_seconds"] = 5
            self.assertEqual(self.client.post(f"/api/chats/{next_chat}/messages", json={"content": "next"}, headers=HEADERS).status_code, 202)
            timed_out = self.wait_idle(active)
            self.assertIn("15분", timed_out["error"])
            self.assertIn("다시", timed_out["messages"][-1]["content"])
            self.assertEqual(self.wait_idle(next_chat)["messages"][-1]["content"], "Finished next")

    def test_restart_marks_unfinished_requests_without_reexecution(self):
        self.login()
        chat_id = self.new_chat()
        message_id = str(uuid.uuid4())
        with service.db() as con:
            con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (message_id, chat_id, "assistant", "Partial answer", "[]", time.time()))
            con.execute("UPDATE chats SET request_state='running',request_message_id=? WHERE id=?", (message_id, chat_id))
        with patch.object(service, "run_agent") as operation:
            service.initialize()
            service.initialize()
            operation.assert_not_called()
        result = self.client.get(f"/api/chats/{chat_id}").json()
        self.assertEqual(result["request_state"], "idle")
        self.assertFalse(result["running"])
        self.assertIn("다시 보내", result["error"])
        self.assertEqual(result["messages"][-1]["content"].count("중단"), 1)
        self.assertTrue(result["messages"][-1]["content"].startswith("Partial answer"))

    def test_cancel_drains_actual_file_thread_before_next_turn(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def slow_tool(name, args):
            started.set()
            try:
                if not release.wait(3):
                    raise TimeoutError("Test barrier")
                return {"ok": True}
            finally:
                finished.set()
        async def exercise():
            queue = service.FifoQueue()
            next_started = asyncio.Event()
            async def following():
                self.assertTrue(finished.is_set())
                next_started.set()
            first = queue.submit("first", "test_a", lambda: service.run_file_tool("write_file", {}))
            second = queue.submit("second", "test_b", following)
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                queue.cancel("first")
                await asyncio.sleep(0)
                self.assertEqual(queue.status("first")["request_state"], "cancelling")
                self.assertFalse(next_started.is_set())
                release.set()
                self.assertEqual(await asyncio.wait_for(first.completion, 3), "cancelled")
                await asyncio.wait_for(second.completion, 3)
                self.assertTrue(next_started.is_set())
            finally:
                release.set()
                await queue.shutdown()
        with patch.object(service, "execute_tool", slow_tool):
            asyncio.run(exercise())

    def test_cancel_closes_model_stream_and_client_before_next_turn(self):
        async def exercise():
            streaming, closing, release, closed, next_started = (asyncio.Event() for _ in range(5))
            class FakeResponse:
                async def __aenter__(self): return self
                async def __aexit__(self, *args):
                    closing.set()
                    await release.wait()
                def raise_for_status(self): pass
                async def aiter_lines(self):
                    streaming.set()
                    await asyncio.Event().wait()
                    yield ""
            class FakeClient:
                def __init__(self, *args, **kwargs): pass
                async def __aenter__(self): return self
                async def __aexit__(self, *args): closed.set()
                def stream(self, *args, **kwargs): return FakeResponse()
            async def on_chunk(content): pass
            async def following():
                self.assertTrue(closed.is_set())
                next_started.set()
            queue = service.FifoQueue()
            with patch.object(service.httpx, "AsyncClient", FakeClient):
                first = queue.submit("first", "test_a", lambda: service.model_turn([], on_chunk))
                second = queue.submit("second", "test_b", following)
                try:
                    await asyncio.wait_for(streaming.wait(), 2)
                    queue.cancel("first")
                    await asyncio.wait_for(closing.wait(), 2)
                    self.assertFalse(next_started.is_set())
                    self.assertFalse(queue.cancel("first"))
                    release.set()
                    await asyncio.wait_for(first.completion, 2)
                    await asyncio.wait_for(second.completion, 2)
                    self.assertTrue(next_started.is_set())
                finally:
                    release.set()
                    await queue.shutdown()
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main(verbosity=2)
