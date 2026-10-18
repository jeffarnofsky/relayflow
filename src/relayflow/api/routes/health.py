from fastapi import APIRouter

router = APIRouter()


@router.get(
    "/health/live",
    tags=["Health"],
    summary="Check whether the API process is alive",
)
def health_live() -> dict[str, str]:
    return {
        "status": "alive",
        "service": "relayflow",
    }
