"""One event-loop-owned FIFO for complete model/agent turns across accounts."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import logging
from typing import Awaitable, Callable

LOG = logging.getLogger(__name__)


class AdmissionError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Entry:
    key: str
    user_id: str
    operation: Callable[[], Awaitable]
    completion: asyncio.Future
    enqueued_at: float
    state: str = "queued"
    reason: str | None = None
    work: asyncio.Task | None = None
    runner: asyncio.Task | None = None
    wait_timer: asyncio.TimerHandle | None = None
    run_timer: asyncio.TimerHandle | None = None


class FifoQueue:
    def __init__(self, *, max_waiting=8, max_pending_per_user=2,
                 max_runtime_seconds=3600, max_wait_seconds=7200,
                 on_state=None, on_finish=None):
        self.limits = dict(max_waiting=max_waiting, max_pending_per_user=max_pending_per_user,
                           max_runtime_seconds=max_runtime_seconds, max_wait_seconds=max_wait_seconds)
        self.on_state, self.on_finish = on_state, on_finish
        self.entries: dict[str, Entry] = {}
        self.waiting = deque()
        self.active: Entry | None = None
        self.closed = False

    def check_available(self, key, user_id):
        # Call admission, durable append, and submit without yielding between
        # them. All callers run in the same application event loop.
        if self.closed:
            raise AdmissionError("closed")
        if key in self.entries:
            raise AdmissionError("duplicate")
        if sum(entry.user_id == user_id for entry in self.entries.values()) >= self.limits["max_pending_per_user"]:
            raise AdmissionError("user_limit")
        if self.active is not None and len(self.waiting) >= self.limits["max_waiting"]:
            raise AdmissionError("queue_full")

    def submit(self, key, user_id, operation):
        self.check_available(key, user_id)
        loop = asyncio.get_running_loop()
        entry = Entry(key, user_id, operation, loop.create_future(), loop.time())
        self.entries[key] = entry
        if self.active is None:
            self._start(entry)
        else:
            self.waiting.append(key)
            entry.wait_timer = loop.call_later(self.limits["max_wait_seconds"], self._expire, entry)
            self._notify(self.on_state, entry)
        return entry

    def status(self, key):
        entry = self.entries.get(key)
        return {"running": entry is not None,
                "request_state": entry.state if entry else "idle",
                "queue_position": list(self.waiting).index(key) + 1 if key in self.waiting else None,
                "queue_waiting": len(self.waiting), "server_busy": self.active is not None}

    def summary(self, user_id):
        return {"server_busy": self.active is not None, "queue_waiting": len(self.waiting),
                "own_pending": sum(entry.user_id == user_id for entry in self.entries.values()),
                "limits": dict(self.limits)}

    @staticmethod
    def _notify(callback, *args):
        if callback:
            try:
                callback(*args)
            except Exception:
                # A persistence failure must not leave an untracked active slot.
                LOG.error("Could not persist inference queue state")

    def _start(self, entry):
        if entry.wait_timer:
            entry.wait_timer.cancel()
        self.active = entry
        entry.state = "running"
        self._notify(self.on_state, entry)
        loop = asyncio.get_running_loop()
        entry.run_timer = loop.call_later(self.limits["max_runtime_seconds"], self.cancel, entry.key, "timeout")
        # This wrapper is never canceled: it waits for operation cleanup before
        # releasing the shared slot, even when cancelled before its first step.
        entry.runner = asyncio.create_task(self._execute(entry))

    async def _execute(self, entry):
        error = None
        reason = "completed"
        try:
            if entry.reason is None:
                entry.work = asyncio.create_task(entry.operation())
                await entry.work
        except asyncio.CancelledError:
            reason = "cancelled"
        except Exception as exc:
            reason, error = "error", exc
        finally:
            self._finish(entry, entry.reason or reason, error)

    def _expire(self, entry):
        if self.entries.get(entry.key) is entry and entry.state == "queued":
            self.cancel(entry.key, "expired")

    def cancel(self, key, reason="cancelled"):
        entry = self.entries.get(key)
        if entry is None or entry.reason is not None:
            return False
        entry.reason = reason
        if entry.state == "queued":
            self.waiting.remove(key)
            self._finish(entry, reason, None)
        else:
            entry.state = "cancelling"
            if entry.run_timer:
                entry.run_timer.cancel()
            self._notify(self.on_state, entry)
            if entry.work is not None and not entry.work.done():
                entry.work.cancel()
        return True

    def _finish(self, entry, reason, error):
        if self.entries.get(entry.key) is not entry:
            return
        for timer in (entry.wait_timer, entry.run_timer):
            if timer:
                timer.cancel()
        entry.state = "idle"
        self._notify(self.on_finish, entry, reason, error)
        self.entries.pop(entry.key)
        if not entry.completion.done():
            entry.completion.set_result(reason)
        if self.active is entry:
            self.active = None
        if self.active is None and not self.closed:
            while self.waiting:
                next_entry = self.entries[self.waiting.popleft()]
                if asyncio.get_running_loop().time() - next_entry.enqueued_at >= self.limits["max_wait_seconds"]:
                    self._finish(next_entry, "expired", None)
                    # _finish may already have promoted the following entry.
                    if self.active is not None:
                        break
                else:
                    self._start(next_entry)
                    break

    async def shutdown(self):
        self.closed = True
        for key in list(self.waiting):
            self.cancel(key, "interrupted")
        active = self.active
        if active is not None:
            self.cancel(active.key, "interrupted")
            await asyncio.shield(active.runner)
