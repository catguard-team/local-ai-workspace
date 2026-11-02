"""Portable stream tests and mocked Linux supervisor tests; no real Docker use."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import relay


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.servers = []
        self.relays = []
        self.writers = []
        self.upstream_tasks = set()

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
        for forwarder in self.relays:
            await asyncio.wait_for(forwarder.shutdown(), 8)
        for writer in self.writers:
            await relay.close_writer(writer)
        tasks = tuple(self.upstream_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for server in self.servers:
            await asyncio.wait_for(server.wait_closed(), 8)

    async def make_relay(self, handler=None, **kwargs):
        port = 1
        if handler:
            async def tracked(reader, writer):
                task = asyncio.current_task()
                self.upstream_tasks.add(task)
                try:
                    await handler(reader, writer)
                finally:
                    await relay.close_writer(writer)
                    self.upstream_tasks.discard(task)
            upstream = await asyncio.start_server(tracked, "127.0.0.1", 0)
            self.servers.append(upstream)
            port = upstream.sockets[0].getsockname()[1]
        forwarder = relay.StreamRelay("127.0.0.1", port, **kwargs)
        self.relays.append(forwarder)
        front = await asyncio.start_server(forwarder.accept, "127.0.0.1", 0)
        self.servers.append(front)
        return forwarder, front.sockets[0].getsockname()[1]

    async def connect(self, port):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.writers.append(writer)
        return reader, writer

    async def test_binary_payload_and_half_close_preserve_complete_response(self):
        payload = bytes(range(256)) * 1024
        async def upstream(reader, writer):
            body = await reader.read()
            writer.write(body[::-1])
            await writer.drain()
        _, port = await self.make_relay(upstream, idle_timeout=2)
        reader, writer = await self.connect(port)
        writer.write(payload)
        await writer.drain()
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), payload[::-1])

    async def test_active_response_can_outlast_idle_interval_with_quiet_request(self):
        async def upstream(reader, writer):
            self.assertEqual(await reader.readexactly(1), b"x")
            for _ in range(10):
                writer.write(b"chunk")
                await writer.drain()
                await asyncio.sleep(0.05)
        _, port = await self.make_relay(upstream, idle_timeout=0.2)
        reader, writer = await self.connect(port)
        writer.write(b"x")
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(50), 3), b"chunk" * 10)

    async def test_idle_connection_is_closed(self):
        async def upstream(reader, writer):
            await reader.read()
        _, port = await self.make_relay(upstream, idle_timeout=0.15)
        reader, _ = await self.connect(port)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")

    async def test_connect_timeout_is_bounded(self):
        cancelled = asyncio.Event()
        async def stalled_connector(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        _, port = await self.make_relay(connect_timeout=0.1, connector=stalled_connector)
        reader, _ = await self.connect(port)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        self.assertTrue(cancelled.is_set())

    async def test_connection_limit_rejects_excess_without_interrupting_first(self):
        connected = asyncio.Event()
        async def upstream(reader, writer):
            connected.set()
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        forwarder, port = await self.make_relay(upstream, max_connections=1)
        first_reader, first_writer = await self.connect(port)
        await asyncio.wait_for(connected.wait(), 2)
        second_reader, _ = await self.connect(port)
        self.assertEqual(await asyncio.wait_for(second_reader.read(), 2), b"")
        first_writer.write(b"still connected")
        await first_writer.drain()
        self.assertEqual(await asyncio.wait_for(first_reader.readexactly(15), 2), b"still connected")
        self.assertEqual(len(forwarder.tasks), 1)

    async def test_shutdown_cancels_open_connections(self):
        connected = asyncio.Event()
        async def upstream(reader, writer):
            connected.set()
            await reader.read()
        forwarder, port = await self.make_relay(upstream)
        reader, _ = await self.connect(port)
        await asyncio.wait_for(connected.wait(), 2)
        await asyncio.wait_for(forwarder.shutdown(), 2)
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
        self.assertEqual(len(forwarder.tasks), 0)


def args_fixture():
    return argparse.Namespace(socket=Path("/run/localai-relay/upstream.sock"),
                              upstream_host="10.8.0.4", upstream_port=8787,
                              connect_timeout=10, idle_timeout=900, max_connections=128)


class SupervisorTests(unittest.TestCase):
    def make_supervisor(self):
        children = []
        def spawn(command, **kwargs):
            child = Mock()
            child.poll.return_value = None
            child.command = command
            children.append(child)
            return child
        cleanup = Mock()
        return relay.Supervisor(args_fixture(), spawn=spawn, cleanup=cleanup), children, cleanup

    def test_namespace_pid_or_inode_change_restarts_only_own_child(self):
        supervisor, children, cleanup = self.make_supervisor()
        first = relay.NamespaceTarget(100, 1000)
        supervisor.reconcile(first, 0)
        supervisor.reconcile(first, 1)
        self.assertEqual(len(children), 1)
        supervisor.reconcile(relay.NamespaceTarget(200, 1001), 2)
        children[0].terminate.assert_called_once()
        self.assertEqual(len(children), 2)
        supervisor.reconcile(relay.NamespaceTarget(200, 1002), 3)
        self.assertEqual(len(children), 3)
        command = children[-1].command
        self.assertIn("--net=/proc/200/ns/net", command)
        self.assertIn("--no-fork", command)
        self.assertNotIn("--mount", command)
        self.assertNotIn("--pid", command)
        self.assertIn("10.8.0.4", command)
        cleanup.assert_called_with(args_fixture().socket)

    def test_missing_container_stops_worker_and_does_not_spawn(self):
        supervisor, children, _ = self.make_supervisor()
        supervisor.reconcile(relay.NamespaceTarget(100, 1000), 0)
        supervisor.reconcile(None, 1)
        supervisor.reconcile(None, 20)
        children[0].terminate.assert_called_once()
        self.assertEqual(len(children), 1)
        self.assertIsNone(supervisor.child)

    def test_exited_child_uses_backoff_then_recovers(self):
        supervisor, children, _ = self.make_supervisor()
        target = relay.NamespaceTarget(100, 1000)
        supervisor.reconcile(target, 0)
        children[0].poll.return_value = 1
        supervisor.reconcile(target, 1)
        supervisor.reconcile(target, 2)
        self.assertEqual(len(children), 1)
        supervisor.reconcile(target, 3)
        self.assertEqual(len(children), 2)
        children[1].poll.return_value = 1
        supervisor.reconcile(target, 4)
        self.assertEqual(supervisor.next_start, 8)

    def test_unresponsive_worker_is_killed_after_grace_period(self):
        supervisor, children, cleanup = self.make_supervisor()
        supervisor.reconcile(relay.NamespaceTarget(100, 1000), 0)
        children[0].wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]
        supervisor.stop_child()
        children[0].kill.assert_called_once()
        cleanup.assert_called_with(args_fixture().socket)

    def test_discovery_reads_only_state_and_checks_actual_namespace_inode(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"Running": True, "Pid": 987}), "")
        with patch.object(relay.subprocess, "run", return_value=result) as run, patch.object(relay.os, "stat", return_value=argparse.Namespace(st_ino=123)) as stat_call:
            self.assertEqual(relay.discover_target("wg-easy"), relay.NamespaceTarget(987, 123))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/docker", "inspect", "--type", "container", "--format", "{{json .State}}", "wg-easy"])
            stat_call.assert_called_once_with("/proc/987/ns/net")

    def test_discovery_fails_closed_for_stopped_invalid_or_missing_container(self):
        for state in ({"Running": False, "Pid": 123}, {"Running": True, "Pid": 0}, {"Running": True, "Pid": True}, {"Running": True, "Pid": "123"}, []):
            result = subprocess.CompletedProcess([], 0, json.dumps(state), "")
            with patch.object(relay.subprocess, "run", return_value=result):
                self.assertIsNone(relay.discover_target("wg-easy"))
        with patch.object(relay.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            self.assertIsNone(relay.discover_target("wg-easy"))

    def test_cleanup_never_deletes_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upstream.sock"
            relay.remove_socket(path)
            path.write_text("existing file", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                relay.remove_socket(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "existing file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
