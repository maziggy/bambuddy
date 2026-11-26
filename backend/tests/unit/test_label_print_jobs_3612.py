"""Label print job store and per-printer worker lanes (#3612)."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.services.label_print_jobs import (
    CANCELLED,
    FAILED_GENERIC,
    MAX_QUEUED_PER_PRINTER,
    LabelPrintService,
    PrintJobStore,
    PrintQueueFull,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestStore:
    def test_unknown_id(self):
        assert PrintJobStore().get("nope") is None

    def test_finished_job_expires_after_the_ttl_but_an_active_one_does_not(self):
        clock = Clock()
        store = PrintJobStore(clock=clock)
        done, active = store.add(1), store.add(1)
        store.finish(done)
        clock.now += 14 * 60
        assert store.get(done.id) is done
        clock.now += 2 * 60  # 16 minutes after it finished
        assert store.get(done.id) is None
        assert store.get(active.id) is active  # not finished, so never expired

    def test_ttl_is_fifteen_minutes(self):
        assert PrintJobStore().ttl_s == 15 * 60 and PrintJobStore().max_jobs == 200

    def test_cap_drops_the_oldest_finished_first(self):
        store = PrintJobStore(max_jobs=3)
        a, b, c = store.add(1), store.add(1), store.add(1)
        store.finish(b)
        d = store.add(1)
        assert store.get(b.id) is None  # the finished one went, not the older active one
        assert all(store.get(j.id) for j in (a, c, d))
        assert len(store) == 3

    def test_unfinished_jobs_are_never_evicted_the_store_refuses_instead(self):
        store = PrintJobStore(max_jobs=2)
        a, b = store.add(1), store.add(1)
        with pytest.raises(PrintQueueFull) as err:
            store.add(1)
        assert str(err.value) == "The label printer has too many jobs waiting; try again later"
        assert store.get(a.id) is a and store.get(b.id) is b and len(store) == 2

    def test_default_cap_is_200_unfinished_then_refused_but_finished_make_room(self):
        store = PrintJobStore()
        jobs = [store.add(1) for _ in range(200)]
        with pytest.raises(PrintQueueFull):
            store.add(1)
        assert all(store.get(j.id) for j in jobs)
        store.finish(jobs[5])
        new = store.add(1)
        assert store.get(jobs[5].id) is None and store.get(new.id) is new

    def test_finish_is_final(self):
        store = PrintJobStore()
        job = store.add(2)
        store.finish(job, error="boom")
        store.finish(job)  # a late "done" must not overwrite the failure
        assert (job.state, job.error, job.printed) == ("failed", "boom", 0)

    def test_to_dict(self):
        job = PrintJobStore().add(3)
        assert job.to_dict() == {"job_id": job.id, "state": "queued", "printed": 0, "total": 3, "error": None}


def explain(exc: BaseException) -> str:
    return f"explained: {exc}"


async def settle(service: LabelPrintService, *jobs, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while any(j.state in ("queued", "printing") for j in jobs):
        assert asyncio.get_running_loop().time() < deadline, [j.to_dict() for j in jobs]
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
class TestService:
    async def test_runs_and_marks_done(self):
        service = LabelPrintService()
        seen = []

        async def work(job):
            seen.append(job.state)

        job = service.submit(("h", 1), 3, work, explain)
        assert job.state == "queued"
        await settle(service, job)
        assert seen == ["printing"]
        assert (job.state, job.printed, job.total, job.error) == ("done", 3, 3, None)

    async def test_more_than_twenty_waiting_jobs_for_one_printer_are_refused(self):
        service = LabelPrintService()
        gate = asyncio.Event()

        async def work(job):
            await gate.wait()

        explain = str
        first = service.submit(("p", 1), 1, work, explain)
        await asyncio.sleep(0)  # the worker takes the first one: it runs, it does not wait
        assert first.state == "printing"
        for _ in range(MAX_QUEUED_PER_PRINTER):
            service.submit(("p", 1), 1, work, explain)
        with pytest.raises(PrintQueueFull):
            service.submit(("p", 1), 1, work, explain)
        service.submit(("q", 1), 1, work, explain)  # another printer is unaffected
        gate.set()
        await service.shutdown()

    async def test_exception_in_the_worker_is_stored_as_failed_and_the_next_job_runs(self):
        service = LabelPrintService()
        order = []

        async def bad(job):
            raise ValueError("kaput")

        async def good(job):
            order.append("good")

        a = service.submit(("h", 1), 1, bad, explain)
        b = service.submit(("h", 1), 1, good, explain)
        await settle(service, a, b)
        assert (a.state, a.error) == ("failed", "explained: kaput")
        assert b.state == "done" and order == ["good"]

    async def test_a_failing_explain_falls_back_to_the_generic_text(self):
        service = LabelPrintService()

        async def bad(job):
            raise ValueError("x")

        def broken(exc):
            raise RuntimeError("explain broke")

        job = service.submit(("h", 1), 1, bad, broken)
        await settle(service, job)
        assert job.error == FAILED_GENERIC

    async def test_fifo_per_printer_and_parallel_across_printers(self):
        service = LabelPrintService()
        log: list[str] = []
        gates = {name: asyncio.Event() for name in "abc"}

        def make(name):
            async def work(job):
                log.append(f"{name}:start")
                await gates[name].wait()
                log.append(f"{name}:end")

            return work

        a = service.submit(("p1", 1), 1, make("a"), explain)
        b = service.submit(("p1", 1), 1, make("b"), explain)
        c = service.submit(("p2", 1), 1, make("c"), explain)
        await asyncio.sleep(0.05)
        # a and c (different printers) run together; b waits behind a.
        assert log == ["a:start", "c:start"]
        assert (a.state, b.state, c.state) == ("printing", "queued", "printing")
        for gate in gates.values():
            gate.set()
        await settle(service, a, b, c)
        assert log.index("a:end") < log.index("b:start")

    async def test_worker_that_dies_is_restarted_for_the_remaining_jobs(self):
        class Boom(BaseException):  # not an Exception: the worker's own handler does not catch it
            pass

        service = LabelPrintService()

        async def dies(job):
            raise Boom

        async def fine(job):
            pass

        a = service.submit(("h", 1), 1, dies, explain)
        b = service.submit(("h", 1), 1, fine, explain)
        await settle(service, a, b)
        assert (a.state, a.error) == ("failed", FAILED_GENERIC)
        assert b.state == "done"
        c = service.submit(("h", 1), 1, fine, explain)  # and the lane still works afterwards
        await settle(service, c)
        assert c.state == "done"

    async def test_idle_lane_is_dropped_and_restarted_on_demand(self):
        service = LabelPrintService()

        async def fine(job):
            pass

        for _ in range(3):
            await settle(service, service.submit(("h", 1), 1, fine, explain))
            await asyncio.sleep(0.01)
            assert service._lanes == {}

    async def test_shutdown_cancels_the_running_and_queued_jobs_as_failed(self):
        service = LabelPrintService()
        started = asyncio.Event()

        async def hang(job):
            started.set()
            await asyncio.sleep(60)

        a = service.submit(("h", 1), 1, hang, explain)
        b = service.submit(("h", 1), 1, hang, explain)
        await started.wait()
        await asyncio.wait_for(service.shutdown(), 5)
        assert (a.state, a.error) == ("failed", CANCELLED)
        assert (b.state, b.error) == ("failed", CANCELLED)

        # Usable again afterwards (a new app start in the same process, or tests).
        async def fine(job):
            pass

        c = service.submit(("h", 1), 1, fine, explain)
        await settle(service, c)
        assert c.state == "done"

    async def test_a_lane_of_a_finished_event_loop_is_replaced(self):
        service = LabelPrintService()

        def in_other_loop():
            async def hang(job):
                await asyncio.sleep(60)

            async def go():
                return service.submit(("h", 1), 1, hang, explain)

            loop = asyncio.new_event_loop()
            try:
                job = loop.run_until_complete(go())
                loop.run_until_complete(asyncio.sleep(0))  # the worker has started
                return job
            finally:
                for task in asyncio.all_tasks(loop):
                    task.cancel()
                loop.run_until_complete(asyncio.sleep(0))
                loop.close()

        stale = await asyncio.to_thread(in_other_loop)

        async def fine(job):
            pass

        job = service.submit(("h", 1), 1, fine, explain)
        await settle(service, job)
        assert job.state == "done"
        assert stale.state == "failed"  # never printed: its loop was gone
