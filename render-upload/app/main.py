"""Shop Tracker backend — start with:  uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .migrate import upgrade
from .routers import auth, job_cards, job_parts, job_photos, stock, users

@asynccontextmanager
async def lifespan(_app: FastAPI):
    upgrade()  # creates missing tables and adds new columns on start
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Sri Sakthivel Agency – Shop Tracker API",
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.is_dev else None,
)

# Only our own website may call this backend, with the login cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def block_forged_requests(request: Request, call_next):
    """Changes must come from our app: it sends this header, other websites cannot."""
    if request.method in ("POST", "PATCH", "DELETE") and request.url.path not in ("/docs", "/openapi.json"):
        if request.headers.get("x-requested-with") != "shop-tracker" and not settings.is_dev:
            return JSONResponse({"detail": "Request blocked."}, status_code=403)
        if request.headers.get("x-requested-with") not in (None, "shop-tracker"):
            return JSONResponse({"detail": "Request blocked."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/health", tags=["system"])
def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(job_cards.router)
app.include_router(job_parts.router)
app.include_router(job_photos.router)
app.include_router(stock.router)
