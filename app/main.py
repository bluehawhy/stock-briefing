import asyncio
import os
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.kakao import router
from app.registration import router as registration_router
from app.dashboard import router as dashboard_router, HEADERS as DASHBOARD_HEADERS
from app.briefings import process_jobs
from app.config import Settings
from app.storage import Store
from app.logs import install_log_capture, log_python_exception
from app.background import run_blocking


async def run_telegram(settings, store):
    from app.telegram import poll
    while True:
        try:
            await poll(settings, store)
            logging.getLogger(__name__).warning('Telegram poll stopped; retrying')
        except Exception as exc:
            logging.getLogger(__name__).warning('Telegram poll stopped (%s); retrying', type(exc).__name__)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    settings = Settings.from_env()
    polling_enabled = os.getenv('TELEGRAM_POLLING_ENABLED', '0') == '1'
    if polling_enabled and (not settings.telegram_bot_token or not settings.telegram_allowed_user_ids):
        raise RuntimeError('Telegram polling configuration is incomplete')
    store = Store(settings.db_path)
    await run_blocking(install_log_capture, store)
    app.state.store = store
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(loop, context):
        exc = context.get("exception")
        if isinstance(exc, Exception):
            log_python_exception("asyncio", exc)
        if previous_handler:
            previous_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)
    task = asyncio.create_task(process_jobs(settings, store))
    stop = asyncio.Event()
    scheduler_task = None
    telegram_task = None
    if polling_enabled:
        telegram_task = asyncio.create_task(run_telegram(settings, store))
    if os.getenv('DAILY_SCHEDULER_ENABLED', '0') == '1':
        from app.scheduler import daily_schedule
        scheduler_task = asyncio.create_task(daily_schedule(settings, store, stop))
    try:
        yield
    finally:
        stop.set()
        if telegram_task is not None:
            telegram_task.cancel()
            with suppress(asyncio.CancelledError):
                await telegram_task
        if scheduler_task is not None:
            await scheduler_task
        loop.set_exception_handler(previous_handler)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="stock-briefing messaging", lifespan=lifespan)
app.include_router(router)
app.include_router(registration_router)
app.include_router(dashboard_router)


@app.middleware("http")
async def dashboard_headers(request, call_next):
    response = await call_next(request)
    if request.url.path == "/stock-briefing" or request.url.path.startswith("/stock-briefing/"):
        response.headers.update(DASHBOARD_HEADERS)
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    store = app.state.store
    with store.connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": "postgres" if store.database_options else "sqlite",
            "daily_scheduler": "enabled" if os.getenv('DAILY_SCHEDULER_ENABLED', '0') == '1' else "disabled"}

