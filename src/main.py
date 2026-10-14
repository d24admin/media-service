import logging
import os
from pathlib import Path
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from errors import MediaError
from routes import router

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level)
logger = logging.getLogger("media-service")
logger.setLevel(log_level)

app = FastAPI(title="Media Service", version="1.0.0")
app.include_router(router)


@app.middleware("http")
async def log_request(request: Request, call_next):
    started_at = time.perf_counter()
    owner_id = request.headers.get("x-user-id", "-")
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed method=%s path=%s owner_id=%s",
            request.method,
            request.url.path,
            owner_id,
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "request_completed method=%s path=%s status_code=%s duration_ms=%.2f owner_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        owner_id,
    )
    return response


@app.get("/", include_in_schema=False)
async def demo_page() -> FileResponse:
    return FileResponse(
        Path(__file__).parent / "static" / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.exception_handler(MediaError)
async def media_error_handler(request: Request, exc: MediaError) -> JSONResponse:
    logger.warning(
        "request_rejected method=%s path=%s status_code=%s code=%s owner_id=%s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.code,
        request.headers.get("x-user-id", "-"),
    )
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.warning(
        "request_rejected method=%s path=%s status_code=400 code=VALIDATION_ERROR owner_id=%s",
        request.method,
        request.url.path,
        request.headers.get("x-user-id", "-"),
    )
    return JSONResponse(status_code=400, content={"error": {"code": "VALIDATION_ERROR", "message": "Invalid request"}})
