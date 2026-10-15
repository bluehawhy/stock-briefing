import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.kakao import router
from app.registration import router as registration_router
from app.dashboard import router as dashboard_router, HEADERS as DASHBOARD_HEADERS
from app.briefings import process_jobs
from app.config import Settings
from app.storage import Store
from app.logs import install_log_capture, log_python_exception


@asynccontextmanager
async def lifespan(app):
    settings = Settings.from_env()
    store = Store(settings.db_path)
    install_log_capture(store)
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
    try:
        yield
    finally:
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
    return {"status": "ok"}
