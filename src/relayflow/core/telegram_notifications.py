from telegram import Bot

from relayflow.models.integration import Integration
from relayflow.models.run import Run

TERMINAL_RUN_STATUSES = {"succeeded", "failed"}


def parse_allowed_telegram_user_ids(raw: str) -> list[int]:
    """Parse a comma-separated list of positive Telegram user IDs."""
    if not raw.strip():
        return []

    user_ids: list[int] = []

    for item in raw.split(","):
        value = item.strip()

        if not value:
            continue

        if not value.isascii() or not value.isdigit():
            raise ValueError("Telegram user IDs must be positive integers.")

        user_id = int(value)

        if user_id <= 0:
            raise ValueError("Telegram user IDs must be positive integers.")

        if user_id not in user_ids:
            user_ids.append(user_id)

    return user_ids


def build_run_notification(
    run: Run,
    integration: Integration,
) -> str:
    """Build a notification that excludes credentials and response bodies."""
    if run.status not in TERMINAL_RUN_STATUSES:
        raise ValueError("Notifications require a terminal Run status.")

    integration_name = " ".join(integration.name.split())[:120]
    status_label = run.status.upper()
    http_status = str(run.http_status_code) if run.http_status_code is not None else "not available"
    duration = f"{run.duration_ms} ms" if run.duration_ms is not None else "not available"

    lines = [
        f"RelayFlow Run: {status_label}",
        f"Integration: {integration_name}",
        f"Run ID: {run.id}",
        f"HTTP status: {http_status}",
        f"Duration: {duration}",
    ]

    if run.status == "failed":
        lines.append("Check the Run record through the RelayFlow API for details.")

    return "\n".join(lines)


async def send_telegram_message(
    token: str,
    chat_id: int,
    message: str,
) -> None:
    """Send one Telegram message and release the client's resources."""
    async with Bot(token=token) as bot:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
        )
