"""Liveness / readiness for orchestration (Docker, Kubernetes, load balancers)."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.database import get_client

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live():
    """Process is up (no dependency checks)."""
    return {"status": "ok"}


@router.get("/ready")
async def ready():
    """Dependencies available — use for traffic routing after replica start."""
    try:
        await get_client().admin.command("ping")
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "detail": str(e)},
        )
    return {"status": "ready", "mongodb": "ok"}
