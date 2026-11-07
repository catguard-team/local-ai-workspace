import json
import asyncio
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock, Mock

import server_control as control


class ServerControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in (("STATE", self.root), ("GATEWAY_STOP", self.root / "gateway-stop"),
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
        control.STOP_STATUS.write_text(json.dumps({"phase": "stopping", "updated_at": time.time()-121}))
        control.GATEWAY_STOP.touch()
        control.prepare_start()
        self.assertFalse(control.GATEWAY_STOP.exists())

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
