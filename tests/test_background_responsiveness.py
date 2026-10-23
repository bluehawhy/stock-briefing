import asyncio
from contextlib import contextmanager
import logging
import threading

import httpx
import pytest
from fastapi import FastAPI

from app.briefings import process_jobs
from app.logs import StoredLogHandler


@pytest.mark.parametrize('phase', ['claim', 'finish'])
def test_web_responds_while_queue_database_is_waiting(monkeypatch, phase):
    entered, release, completed = (threading.Event() for _ in range(3))
    calls = []
    loop_thread = threading.get_ident()

    def wait():
        calls.append(threading.get_ident())
        entered.set()
        release.wait(2)
        completed.set()

    class Store:
        def claim_job(self):
            if phase == 'claim':
                wait()
                return None
            return 1

        def finish_job(self, *args):
            wait()

    class Engine:
        def __init__(self, *args): pass
        def generate(self): pass

    monkeypatch.setattr('app.briefings.BriefingEngine', Engine)
    api = FastAPI()

    @api.get('/ping')
    async def ping():
        return {'ok': True}

    async def run():
        worker = asyncio.create_task(process_jobs(None, Store()))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
                assert (await client.get('/ping')).json() == {'ok': True}
            assert not completed.is_set(), 'web request waited for synchronous database work'
            worker.cancel()
            await asyncio.sleep(0)
            assert not worker.done(), 'shutdown abandoned the transaction thread'
        finally:
            release.set()
            worker.cancel() if not worker.cancelling() else None
            with pytest.raises(asyncio.CancelledError):
                await worker
        assert completed.is_set()

    asyncio.run(run())
    assert calls and all(thread != loop_thread for thread in calls)


def test_claim_failure_recovers_without_replaying_generation(monkeypatch):
    finished = threading.Event()
    claims, generations = [], []
    original_sleep = asyncio.sleep

    class Store:
        def claim_job(self):
            claims.append(1)
            if len(claims) == 1:
                raise ConnectionError('unavailable')
            return 1 if len(claims) == 2 else None

        def finish_job(self, job, status, detail):
            assert (job, status) == (1, 'done')
            finished.set()

    class Engine:
        def __init__(self, *args): pass
        def generate(self): generations.append(1)

    delays = []
    async def sleep(delay):
        delays.append(delay)
        await original_sleep(0.001)

    monkeypatch.setattr('app.briefings.BriefingEngine', Engine)
    monkeypatch.setattr('app.briefings.asyncio.sleep', sleep)
    async def run():
        worker = asyncio.create_task(process_jobs(None, Store()))
        try:
            assert await asyncio.to_thread(finished.wait, 2)
        finally:
            worker.cancel()
            with pytest.raises(asyncio.CancelledError): await worker
    asyncio.run(run())
    assert 5 in delays
    assert generations == [1]


def test_event_loop_logging_does_not_wait_for_database():
    entered, release, done = (threading.Event() for _ in range(3))
    class DB:
        def execute(self, *args): pass
    class Store:
        @contextmanager
        def connect(self):
            entered.set()
            release.wait(2)
            try: yield DB()
            finally: done.set()
    handler = StoredLogHandler(Store())
    async def run():
        try:
            handler.handle(logging.LogRecord('app.scheduler', logging.INFO, __file__, 1,
                                            'Daily scheduler started: test', (), None))
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0)
            assert not done.is_set()
        finally:
            release.set()
    asyncio.run(run())
    assert done.is_set()


def test_worker_log_database_wait_does_not_lock_out_event_loop_logging():
    entered, release = threading.Event(), threading.Event()
    class DB:
        def execute(self, *args): pass
    class Store:
        @contextmanager
        def connect(self):
            entered.set()
            release.wait(2)
            yield DB()
    handler = StoredLogHandler(Store())
    record = logging.LogRecord('app.scheduler', logging.INFO, __file__, 1,
                               'Daily scheduler started: test', (), None)
    writer = threading.Thread(target=handler.handle, args=(record,))
    async def run():
        writer.start()
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            handler.handle(record)
            assert writer.is_alive(), 'event loop waited for the worker logging lock'
        finally:
            release.set()
            await asyncio.to_thread(writer.join)
    asyncio.run(run())
