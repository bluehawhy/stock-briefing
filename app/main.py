from fastapi import FastAPI

from app.kakao import router

app = FastAPI(title="stock-briefing messaging")
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
