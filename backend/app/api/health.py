from fastapi import APIRouter, Response, status

from app.core.health import check_dependencies

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Always 200. What the Next.js page consumes, so a dead dependency has to be
    readable as data rather than as a failed fetch."""
    deps = await check_dependencies()
    overall = "ok" if all(v == "ok" for v in deps.values()) else "degraded"
    return {"status": overall, **deps}


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, str]:
    """503 if any dependency is down. Railway's healthcheck target: always-200 as
    the only contract means a dead Postgres reads as a healthy deploy and traffic
    keeps being routed to it."""
    deps = await check_dependencies()
    ok = all(v == "ok" for v in deps.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ok else "not_ready", **deps}
