"""Run synchronous work without blocking ASGI; drain it before cancellation."""
import asyncio


async def run_blocking(function, *args, **kwargs):
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        # A worker thread cannot be cancelled. Let its transaction/lease finish.
        try:
            await work
        except Exception:
            pass
        raise
