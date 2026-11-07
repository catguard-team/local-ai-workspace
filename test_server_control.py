import json
import asyncio
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock, Mock

import server_control as control


class ServerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in (("STATE", self.root), ("GATEWAY_STOP", self.root / "gateway-stop"),
                            ("STOP_REQUEST", self.root / "server-stop-request"),
                            ("STOP_STATUS", self.root / "server-stop-status.json")):
            patcher = patch.object(control, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_shutdown_targets_only_fixed_model_and_lm_api(self):
        def fake_run(command, **kwargs):
            self.assertEqual(kwargs["timeout"], 30)
            return subprocess.CompletedProcess(command, 0, '[{"identifier":"qwen3.8-27b"},{"identifier":"unrelated"}]')
        with patch.object(control.subprocess, "run", side_effect=fake_run) as runner:
            control.stop_local_services()
        self.assertEqual([c.args[0][1:] for c in runner.call_args_list],
                         [["ps", "--json"], ["unload", "qwen3.8-27b"], ["server", "stop"]])
        self.assertTrue(control.GATEWAY_STOP.exists())
        self.assertEqual(json.loads(control.STOP_STATUS.read_text())["phase"], "stopped")
        control.prepare_start()
        self.assertFalse(control.GATEWAY_STOP.exists())

    def test_cli_failure_is_recorded_and_does_not_skip_api_stop(self):
        with patch.object(control.subprocess, "run", side_effect=FileNotFoundError) as runner:
            control.stop_local_services()
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(json.loads(control.STOP_STATUS.read_text())["phase"], "failed")
        self.assertTrue(control.GATEWAY_STOP.exists())

    def test_start_waits_for_live_shutdown_but_recovers_stale_status(self):
        control.write_status("stopping")
        with self.assertRaises(RuntimeError):
            control.prepare_start()
        control.STOP_STATUS.write_text(json.dumps({"phase": "stopping", "updated_at": time.time()-301}))
        control.GATEWAY_STOP.touch()
        control.prepare_start()
        self.assertFalse(control.GATEWAY_STOP.exists())

    def test_direct_request_waits_for_backend_cleanup_and_all_ports_closed(self):
        ports = {8081, 8787, 1234}
        def finish(_):
            self.assertTrue(control.STOP_REQUEST.exists())
            self.assertEqual(control.read_status()["phase"], "requested")
            ports.clear()
            control.write_status("stopped")
        with patch.object(control, "lifecycle_lock", return_value=nullcontext()), \
             patch.object(control, "port_open", side_effect=lambda p: p in ports), \
             patch.object(control, "stop_local_services") as fallback, \
             patch.object(control.time, "sleep", side_effect=finish):
            control.request_local_stop()
        fallback.assert_not_called()

    def test_partial_server_is_stopped_and_repeat_is_idempotent(self):
        ports = {8787, 1234}
        def finish():
            ports.clear()
            control.write_status("stopped")
        with patch.object(control, "lifecycle_lock", return_value=nullcontext()), \
             patch.object(control, "port_open", side_effect=lambda p: p in ports), \
             patch.object(control, "stop_local_services", side_effect=finish) as fallback:
            control.request_local_stop()
            control.request_local_stop()
        fallback.assert_called_once()

    def test_old_success_or_live_ports_never_report_success(self):
        control.write_status("stopped")
        with patch.object(control, "lifecycle_lock", return_value=nullcontext()), \
             patch.object(control, "port_open", return_value=True), \
             patch.object(control, "stop_local_services") as fallback:
            with self.assertRaisesRegex(RuntimeError, "has not completed"):
                control.request_local_stop(timeout=0)
        fallback.assert_not_called()
        self.assertTrue(control.STOP_REQUEST.exists())

    def test_stop_failure_is_reported(self):
        with patch.object(control, "lifecycle_lock", return_value=nullcontext()), \
             patch.object(control, "port_open", return_value=False), \
             patch.object(control, "stop_local_services", side_effect=lambda: control.write_status("failed", ["CLI unavailable"])):
            with self.assertRaisesRegex(RuntimeError, "CLI unavailable"):
                control.request_local_stop()

    def test_start_does_not_clear_request_while_cleanup_is_live(self):
        control.STOP_REQUEST.touch()
        control.STOP_STATUS.write_text(json.dumps({"phase": "stopping", "updated_at": time.time()-301}))
        with patch.object(control, "port_open", return_value=True):
            with self.assertRaises(RuntimeError):
                control.prepare_start()
        self.assertTrue(control.STOP_REQUEST.exists())
        control.write_status("stopped")
        control.prepare_start()
        self.assertFalse(control.STOP_REQUEST.exists())

    def test_windows_lifecycle_lock_serializes_threads(self):
        failures = []
        def contend():
            try:
                with control.lifecycle_lock(timeout=0):
                    failures.append("concurrent acquisition")
            except RuntimeError:
                pass
        with control.lifecycle_lock(timeout=0):
            worker = threading.Thread(target=contend)
            worker.start()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        with control.lifecycle_lock(timeout=0):
            pass

    def test_local_signal_cancels_queue_and_waits_for_file_cleanup(self):
        from inference_queue import FifoQueue, AdmissionError
        async def exercise():
            queue = FifoQueue()
            started, cleaning, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
            invoked = []
            async def active():
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cleaning.set()
                    await cleaned.wait()
            async def waiting():
                invoked.append("queued work started")
            running = queue.submit("active", "a", active)
            queued = queue.submit("waiting", "b", waiting)
            await started.wait()
            callback = Mock()
            app = SimpleNamespace(state=SimpleNamespace(request_shutdown=callback))
            control.STOP_REQUEST.touch()
            watcher = asyncio.create_task(control.watch_stop_request(queue, app))
            await asyncio.wait_for(cleaning.wait(), 2)
            self.assertTrue(queue.closed)
            self.assertFalse(watcher.done())
            callback.assert_not_called()
            with self.assertRaises(AdmissionError):
                queue.submit("new", "c", waiting)
            cleaned.set()
            await asyncio.wait_for(watcher, 2)
            self.assertEqual(await running.completion, "interrupted")
            self.assertEqual(await queued.completion, "interrupted")
            self.assertEqual(invoked, [])
            self.assertEqual(queue.entries, {})
            callback.assert_called_once()
            self.assertTrue(app.state.shutdown_requested)
        asyncio.run(exercise())

    def test_gateway_stop_closes_listener_and_cleans_up(self):
        import gateway
        runner = Mock(setup=AsyncMock(), cleanup=AsyncMock())
        site = Mock(start=AsyncMock())
        with patch.object(gateway, "GATEWAY_STOP", control.GATEWAY_STOP), \
             patch.object(gateway.web, "AppRunner", return_value=runner), \
             patch.object(gateway.web, "TCPSite", return_value=site), \
             patch.object(gateway, "app", {"vpn_config": None}), \
             patch.object(gateway, "bind_vpn_when_ready", new=AsyncMock()):
            control.GATEWAY_STOP.touch()
            asyncio.run(gateway.run_server())
        site.start.assert_awaited_once()
        runner.cleanup.assert_awaited_once()
