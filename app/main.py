from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request

from app.config import (
    CORS_ORIGINS_RAW,
    DB_NAME,
    DEFAULT_PLACEHOLDER_SECRET,
    ROTASHIFT_ENV,
    SECRET_KEY,
)
from app.routers import activities_api, admin_api, auth_api, departments_api, health_api, meta_api, requests_api, shifts_api, tasks_api, users_api
from app.seed import ensure_indexes_and_seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    if ROTASHIFT_ENV == "production" and SECRET_KEY == DEFAULT_PLACEHOLDER_SECRET:
        raise RuntimeError(
            "ROTASHIFT_SECRET_KEY must be set to a strong random value when ROTASHIFT_ENV=production "
            "(e.g. openssl rand -hex 32)."
        )
    try:
        await ensure_indexes_and_seed()
        print(f"RotaShift: MongoDB OK — database={DB_NAME!r} (users are in the {DB_NAME!r}.users collection).")
        print("RotaShift: My Kanban API enabled — GET /api/tasks, GET /api/tasks/health")
    except Exception as e:
        print("\n" + "=" * 60)
        print("RotaShift: MongoDB connection failed — the app cannot start.")
        print("Fix: Start MongoDB locally, or set MONGO_URI to your Atlas/cluster URL.")
        print(f"Details: {type(e).__name__}: {e}")
        print("=" * 60 + "\n")
        raise
    yield


ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="RotaShift", lifespan=lifespan)

_origins = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Smaller JSON payloads on slow networks (Render); safe for all environments.
app.add_middleware(GZipMiddleware, minimum_size=900)


class _CacheControlMiddleware(BaseHTTPMiddleware):
    """Long-cache immutable static assets; avoid stale HTML shell after deploy."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path in ("/manifest.json", "/sw.js"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        elif path in ("/", "/app"):
            response.headers.setdefault("Cache-Control", "private, no-cache")
        return response


app.add_middleware(_CacheControlMiddleware)

app.include_router(health_api.router)
app.include_router(auth_api.router)
app.include_router(tasks_api.router)
app.include_router(departments_api.router)
app.include_router(users_api.router)
app.include_router(shifts_api.router)
app.include_router(requests_api.router)
app.include_router(meta_api.router)
app.include_router(admin_api.router)
app.include_router(activities_api.router)

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/manifest.json")
def manifest():
    return FileResponse(ROOT / "static" / "manifest.json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(ROOT / "static" / "sw.js", media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.head("/")
def index_head():
    """Render and other probes often use HEAD; avoid 405 in logs."""
    return Response(status_code=200, media_type="text/html")


@app.get("/app")
def app_page():
    return FileResponse(ROOT / "static" / "index.html")


@app.head("/app")
def app_page_head():
    return Response(status_code=200, media_type="text/html")
