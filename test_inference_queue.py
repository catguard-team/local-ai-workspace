"""Deterministic scheduler tests: synthetic operations, no model or user state."""
import asyncio
import unittest

from inference_queue import AdmissionError, FifoQueue


class InferenceQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []
        self.gates = []
        self.finished = []
        self.states = []

    def gate(self):
        event = asyncio.Event()
        self.gates.append(event)
        return event

    def queue(self, **limits):
        queue = FifoQueue(
            on_state=lambda entry: self.states.append((entry.key, entry.state)),
            on_finish=lambda entry, reason, error: self.finished.append((entry.key, reason, error)),
            **limits,
        )
        self.queues.append(queue)
        return queue

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        for queue in reversed(self.queues):
            await asyncio.wait_for(queue.shutdown(), 2)

    async def wait(self, event):
        await asyncio.wait_for(event.wait(), 2)

    async def completion(self, entry):
        return await asyncio.wait_for(asyncio.shield(entry.completion), 2)

    def assert_rejected(self, queue, key, user, reason):
        with self.assertRaises(AdmissionError) as error:
            queue.check_available(key, user)
        self.assertEqual(error.exception.reason, reason)

    async def test_fifo_across_accounts_holds_one_active_slot(self):
        queue = self.queue()
        order, concurrency = [], [0, 0]
        release = {key: self.gate() for key in ("a1", "b1", "a2")}
        entered = {key: self.gate() for key in release}

        def operation(key):
            async def run():
                concurrency[0] += 1
                concurrency[1] = max(concurrency)
                order.append(key)
                entered[key].set()
                try:
                    await release[key].wait()
                finally:
                    concurrency[0] -= 1
            return run

        a1 = queue.submit("a1", "alice", operation("a1"))
        await self.wait(entered["a1"])
        b1 = queue.submit("b1", "bob", operation("b1"))
        a2 = queue.submit("a2", "alice", operation("a2"))
        self.assertEqual(order, ["a1"])
        self.assertEqual(queue.status("b1")["queue_position"], 1)
        self.assertEqual(queue.status("a2")["queue_position"], 2)
        self.assertEqual(queue.summary("alice")["own_pending"], 2)
        release["a1"].set()
        await self.wait(entered["b1"])
        self.assertEqual(queue.status("a2")["queue_position"], 1)
        self.assertFalse(entered["a2"].is_set())
        release["b1"].set()
        await self.wait(entered["a2"])
        release["a2"].set()
        for entry in (a1, b1, a2):
            self.assertEqual(await self.completion(entry), "completed")
        self.assertEqual(order, ["a1", "b1", "a2"])
        self.assertEqual(concurrency, [0, 1])
        self.assertEqual([item[:2] for item in self.finished], [("a1", "completed"), ("b1", "completed"), ("a2", "completed")])
        self.assertFalse(queue.summary("alice")["server_busy"])

    async def test_admission_caps_are_atomic_and_include_active_user_request(self):
        queue = self.queue(max_waiting=2, max_pending_per_user=2)
        release = self.gate()

        async def work():
            await release.wait()

        active = queue.submit("a1", "alice", work)
        waiting = queue.submit("a2", "alice", work)
        self.assert_rejected(queue, "a1", "bob", "duplicate")
        self.assert_rejected(queue, "a3", "alice", "user_limit")
        other = queue.submit("b1", "bob", work)
        self.assert_rejected(queue, "c1", "carol", "queue_full")
        with self.assertRaises(AdmissionError) as error:
            queue.submit("c1", "carol", work)
        self.assertEqual(error.exception.reason, "queue_full")
        self.assertFalse(queue.status("c1")["running"])
        self.assertEqual(queue.summary("alice")["queue_waiting"], 2)
        self.assertTrue(queue.cancel("a2"))
        self.assertEqual(await self.completion(waiting), "cancelled")
        replacement = queue.submit("c1", "carol", work)
        self.assertEqual(queue.status("b1")["queue_position"], 1)
        self.assertEqual(queue.status("c1")["queue_position"], 2)
        release.set()
        for entry in (active, other, replacement):
            self.assertEqual(await self.completion(entry), "completed")

    async def test_default_global_limit_has_eight_waiting_slots(self):
        queue = self.queue()
        release = self.gate()

        async def work():
            await release.wait()

        entries = [queue.submit("active", "active-user", work)]
        entries.extend(queue.submit(f"waiting-{i}", f"user-{i // 2}", work) for i in range(8))
        self.assertEqual(queue.summary("observer")["queue_waiting"], 8)
        self.assert_rejected(queue, "overflow", "new-user", "queue_full")
        release.set()
        for entry in entries:
            self.assertEqual(await self.completion(entry), "completed")

    async def test_queued_cancel_never_invokes_operation_and_frees_position(self):
        queue = self.queue()
        release, entered = self.gate(), self.gate()
        calls = []

        async def active_work():
            entered.set()
            await release.wait()

        async def cancelled_work():
            calls.append("cancelled")

        async def next_work():
            calls.append("next")

        active = queue.submit("active", "alice", active_work)
        await self.wait(entered)
        removed = queue.submit("removed", "bob", cancelled_work)
        following = queue.submit("following", "carol", next_work)
        self.assertTrue(queue.cancel("removed"))
        self.assertFalse(queue.cancel("removed"))
        self.assertEqual(await self.completion(removed), "cancelled")
        self.assertEqual(queue.status("following")["queue_position"], 1)
        release.set()
        await self.completion(active)
        await self.completion(following)
        self.assertEqual(calls, ["next"])
        self.assertEqual(sum(key == "removed" for key, _, _ in self.finished), 1)

    async def test_active_cancel_waits_for_cleanup_and_ignores_repeated_cancel(self):
        queue = self.queue()
        entered, cleaning, release_cleanup, next_entered = (self.gate() for _ in range(4))
        order = []

        async def active_work():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                order.append("cleanup-start")
                cleaning.set()
                await release_cleanup.wait()
                order.append("cleanup-end")
                raise

        async def next_work():
            order.append("next")
            next_entered.set()

        active = queue.submit("active", "alice", active_work)
        await self.wait(entered)
        following = queue.submit("next", "bob", next_work)
        self.assertTrue(queue.cancel("active"))
        await self.wait(cleaning)
        self.assertFalse(queue.cancel("active"))
        self.assertFalse(queue.cancel("active", "timeout"))
        self.assertEqual(queue.status("active")["request_state"], "cancelling")
        self.assertFalse(next_entered.is_set())
        self.assertFalse(active.completion.done())
        self.assertEqual(queue.summary("alice")["own_pending"], 1)
        release_cleanup.set()
        self.assertEqual(await self.completion(active), "cancelled")
        self.assertEqual(await self.completion(following), "completed")
        self.assertEqual(order, ["cleanup-start", "cleanup-end", "next"])

    async def test_cancel_before_first_step_finishes_without_calling_factory(self):
        queue = self.queue()
        calls = []

        async def work():
            calls.append("unexpected")

        active = queue.submit("immediate", "alice", work)
        self.assertTrue(queue.cancel("immediate"))
        self.assertEqual(await self.completion(active), "cancelled")
        self.assertEqual(calls, [])
        self.assertFalse(queue.status("immediate")["running"])
        self.assertEqual([item[:2] for item in self.finished], [("immediate", "cancelled")])

    async def test_wall_deadline_stops_active_and_promotes_next(self):
        # Leave several Windows event-loop ticks for the successor to start.
        queue = self.queue(max_runtime_seconds=.2)
        entered, cleaned = self.gate(), self.gate()

        async def active_work():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        async def next_work():
            self.assertTrue(cleaned.is_set())

        active = queue.submit("slow", "alice", active_work)
        await self.wait(entered)
        following = queue.submit("next", "bob", next_work)
        self.assertEqual(await self.completion(active), "timeout")
        self.assertEqual(await self.completion(following), "completed")

    async def test_wait_deadline_expires_while_active_operation_still_runs(self):
        queue = self.queue(max_wait_seconds=.03)
        release, entered = self.gate(), self.gate()
        calls = []

        async def active_work():
            entered.set()
            await release.wait()

        async def waiting_work():
            calls.append("unexpected")

        active = queue.submit("active", "alice", active_work)
        await self.wait(entered)
        waiting = queue.submit("waiting", "bob", waiting_work)
        self.assertEqual(await self.completion(waiting), "expired")
        self.assertTrue(queue.status("active")["running"])
        self.assertEqual(queue.summary("bob")["own_pending"], 0)
        self.assertEqual(queue.summary("alice")["queue_waiting"], 0)
        release.set()
        self.assertEqual(await self.completion(active), "completed")
        self.assertEqual(calls, [])

    async def test_operation_failure_is_reported_once_and_next_request_runs(self):
        queue = self.queue()
        started = []
        expected = RuntimeError("synthetic model failure")

        async def fail():
            started.append("failure")
            raise expected

        async def recover():
            started.append("recovery")

        failed = queue.submit("failure", "alice", fail)
        following = queue.submit("recovery", "bob", recover)
        self.assertEqual(await self.completion(failed), "error")
        self.assertEqual(await self.completion(following), "completed")
        self.assertEqual(started, ["failure", "recovery"])
        self.assertEqual(self.finished, [("failure", "error", expected), ("recovery", "completed", None)])

    async def test_shutdown_closes_admission_drains_active_and_interrupts_waiting(self):
        queue = self.queue()
        entered, cleaning, release_cleanup = (self.gate() for _ in range(3))
        waiting_calls = []

        async def active_work():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cleaning.set()
                await release_cleanup.wait()
                raise

        async def waiting_work():
            waiting_calls.append("unexpected")

        active = queue.submit("active", "alice", active_work)
        await self.wait(entered)
        waiting = queue.submit("waiting", "bob", waiting_work)
        shutting_down = asyncio.create_task(queue.shutdown())
        await self.wait(cleaning)
        self.assert_rejected(queue, "new", "carol", "closed")
        self.assertEqual(await self.completion(waiting), "interrupted")
        self.assertFalse(shutting_down.done())
        release_cleanup.set()
        await asyncio.wait_for(shutting_down, 2)
        self.assertEqual(await self.completion(active), "interrupted")
        self.assertEqual(waiting_calls, [])
        self.assertEqual(queue.summary("alice")["own_pending"], 0)
        self.assertFalse(queue.summary("alice")["server_busy"])

    async def test_status_summary_exposes_only_counts_for_other_accounts(self):
        queue = self.queue()
        release = self.gate()

        async def work():
            await release.wait()

        active = queue.submit("private-chat-a", "private-user-a", work)
        waiting = queue.submit("private-chat-b", "private-user-b", work)
        summary = queue.summary("observer")
        self.assertEqual(set(summary), {"server_busy", "queue_waiting", "own_pending", "limits"})
        self.assertEqual(summary["own_pending"], 0)
        self.assertEqual(summary["queue_waiting"], 1)
        self.assertNotIn("private", str(summary))
        summary["limits"]["max_waiting"] = 999
        self.assertEqual(queue.summary("observer")["limits"]["max_waiting"], 8)
        release.set()
        await self.completion(active)
        await self.completion(waiting)


if __name__ == "__main__":
    unittest.main(verbosity=2)
