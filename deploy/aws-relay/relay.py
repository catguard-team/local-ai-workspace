#!/usr/bin/env python3
"""Nginx Unix socket -> TCP inside the running wg-easy network namespace.

No HTTP parsing, credentials, request logging, Docker modification, or routing
changes. Only the supervisor needs root; the stream worker drops to www-data.
Requires Linux/Python 3.12 in production. Stream tests also run on Windows.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time

LOG = logging.getLogger("localai-relay")
SOCKET_PATH = Path("/run/localai-relay/upstream.sock")
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def remove_socket(path: Path) -> None:
    """Never replace or remove an unexpected regular file or symlink."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        raise RuntimeError("Relay socket path is occupied by a non-socket")
    path.unlink()


async def close_writer(writer) -> None:
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=5)
    except (OSError, TimeoutError):
        pass


class StreamRelay:
    def __init__(self, host, port, *, connect_timeout=10, idle_timeout=900,
                 max_connections=128, connector=None):
        self.host, self.port = host, port
        self.connect_timeout, self.idle_timeout = connect_timeout, idle_timeout
        self.max_connections = max_connections
        self.connector = connector or asyncio.open_connection
        self.tasks = set()

    def accept(self, reader, writer):
        if len(self.tasks) >= self.max_connections:
            writer.close()
            return
        task = asyncio.create_task(self.forward(reader, writer))
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            # Also closes a session canceled before its coroutine first runs.
            writer.close()
        task.add_done_callback(finished)

    async def shutdown(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def forward(self, client_reader, client_writer):
        upstream_writer = None
        pending = []
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                self.connector(self.host, self.port, limit=65536), self.connect_timeout)
            loop = asyncio.get_running_loop()
            last_activity = loop.time()

            async def pump(reader, writer):
                nonlocal last_activity
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        if writer.can_write_eof():
                            writer.write_eof()
                            await asyncio.wait_for(writer.drain(), self.idle_timeout)
                        return
                    last_activity = loop.time()
                    writer.write(chunk)
                    await asyncio.wait_for(writer.drain(), self.idle_timeout)
                    last_activity = loop.time()

            async def watch_idle():
                while True:
                    remaining = self.idle_timeout - (loop.time() - last_activity)
                    if remaining <= 0:
                        return
                    await asyncio.sleep(remaining)

            # A quiet request direction must not interrupt a streaming response.
            # EOF in one direction is a half-close; keep draining the other.
            transfers = asyncio.gather(pump(client_reader, upstream_writer),
                                       pump(upstream_reader, client_writer))
            idle = asyncio.create_task(watch_idle())
            pending = [transfers, idle]
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for completed in done:
                completed.result()
        except (OSError, TimeoutError):
            # Nginx observes EOF/connection failure. Do not log HTTP or tokens.
            pass
        finally:
            # Closing transports must happen synchronously, even if shutdown
            # cancels a session while its asynchronous cleanup is in progress.
            client_writer.close()
            if upstream_writer is not None:
                upstream_writer.close()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(close_writer(client_writer), close_writer(upstream_writer))


@dataclass(frozen=True)
class NamespaceTarget:
    pid: int
    inode: int


def discover_target(container: str) -> NamespaceTarget | None:
    """Inspect only State; never retrieve Docker environment/secret fields."""
    try:
        result = subprocess.run(
            ["/usr/bin/docker", "inspect", "--type", "container", "--format", "{{json .State}}", container],
            capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            return None
        state = json.loads(result.stdout)
        pid = state.get("Pid")
        if state.get("Running") is not True or type(pid) is not int or pid <= 1:
            return None
        return NamespaceTarget(pid, os.stat(f"/proc/{pid}/ns/net").st_ino)
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def worker_command(target, args):
    return ["/usr/bin/nsenter", "--no-fork", f"--net=/proc/{target.pid}/ns/net",
            "/usr/bin/python3", "-u", str(Path(__file__).resolve()), "worker",
            "--socket", str(args.socket), "--upstream-host", args.upstream_host,
            "--upstream-port", str(args.upstream_port),
            "--connect-timeout", str(args.connect_timeout),
            "--idle-timeout", str(args.idle_timeout),
            "--max-connections", str(args.max_connections)]


class Supervisor:
    def __init__(self, args, *, spawn=None, cleanup=None):
        self.args = args
        self.spawn = spawn or subprocess.Popen
        self.cleanup = cleanup or remove_socket
        self.child = None
        self.target = None
        self.started_at = 0.0
        self.next_start = 0.0
        self.retry_delay = 2.0

    def stop_child(self):
        child, self.child = self.child, None
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            else:
                child.wait()
        self.cleanup(self.args.socket)

    def reconcile(self, target, now):
        if target != self.target:
            self.stop_child()
            self.target = target
            self.next_start, self.retry_delay = now, 2.0
            LOG.info("WireGuard namespace %s", "available" if target else "unavailable")
        if self.child is not None and self.child.poll() is not None:
            self.stop_child()
            if now - self.started_at >= 30:
                self.retry_delay = 2.0
            self.next_start = now + self.retry_delay
            self.retry_delay = min(self.retry_delay * 2, 30)
            LOG.warning("Relay worker exited; retry scheduled")
        if target is not None and self.child is None and now >= self.next_start:
            self.cleanup(self.args.socket)
            self.child = self.spawn(worker_command(target, self.args), stdin=subprocess.DEVNULL)
            self.started_at = now
            LOG.info("Relay worker started")


def supervise(args):
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    supervisor = Supervisor(args)
    try:
        while not stop.is_set():
            supervisor.reconcile(discover_target(args.container), time.monotonic())
            stop.wait(2)
    finally:
        supervisor.stop_child()


async def run_worker(args):
    import grp
    import pwd

    account = pwd.getpwnam("www-data")
    group = grp.getgrnam("www-data")
    remove_socket(args.socket)
    relay = StreamRelay(args.upstream_host, args.upstream_port,
                        connect_timeout=args.connect_timeout, idle_timeout=args.idle_timeout,
                        max_connections=args.max_connections)
    server = await asyncio.start_unix_server(relay.accept, path=str(args.socket),
                                            limit=65536, backlog=128, start_serving=False)
    try:
        os.chown(args.socket, 0, group.gr_gid)
        os.chmod(args.socket, 0o660)
        os.setgroups([])
        os.setgid(group.gr_gid)
        os.setuid(account.pw_uid)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        await server.start_serving()
        LOG.info("Relay socket ready")
        await stop.wait()
    finally:
        server.close()
        await relay.shutdown()
        await server.wait_closed()
        # The root supervisor owns the directory and removes the stale socket.


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("supervise", "worker"))
    parser.add_argument("--container", default="wg-easy")
    parser.add_argument("--socket", type=Path, default=SOCKET_PATH)
    parser.add_argument("--upstream-host", default="10.77.0.2")
    parser.add_argument("--upstream-port", type=int, default=8787)
    parser.add_argument("--connect-timeout", type=float, default=10)
    parser.add_argument("--idle-timeout", type=float, default=900)
    parser.add_argument("--max-connections", type=int, default=128)
    args = parser.parse_args(argv)
    try:
        address = ipaddress.IPv4Address(args.upstream_host)
        if not any(address in network for network in PRIVATE_NETWORKS):
            raise ValueError()
        if (str(address) != args.upstream_host or not 1 <= args.upstream_port <= 65535
                or not 0 < args.connect_timeout <= 60 or not 0 < args.idle_timeout <= 3600
                or not 1 <= args.max_connections <= 1024 or not args.socket.is_absolute()):
            raise ValueError()
    except ValueError:
        parser.error("Expected a private IPv4 upstream, valid port/timeouts and absolute socket path")
    return args


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        raise SystemExit("Production relay must start as root on Linux")
    if args.mode == "supervise":
        supervise(args)
    else:
        asyncio.run(run_worker(args))


if __name__ == "__main__":
    main()
