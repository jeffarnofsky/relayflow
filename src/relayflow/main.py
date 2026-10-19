from fastapi import FastAPI

from relayflow.api.routes.health import router as health_router
from relayflow.api.routes.integrations import router as integrations_router

app = FastAPI(
    title="RelayFlow",
    description=(
        "A platform for API integrations, background automation, "
        "webhook processing, and Telegram notifications."
    ),
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(integrations_router, prefix="/api/v1")
