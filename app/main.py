import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.kakao import router
from app.registration import router as registration_router
from app.briefings import process_jobs
from app.config import Settings
from app.storage import Store
from app.logs import install_log_capture


@asynccontextmanager
async def lifespan(app):
    settings = Settings.from_env()
    store = Store(settings.db_path)
    install_log_capture(store)
    task = asyncio.create_task(process_jobs(settings, store))
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="stock-briefing messaging", lifespan=lifespan)
app.include_router(router)
app.include_router(registration_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
