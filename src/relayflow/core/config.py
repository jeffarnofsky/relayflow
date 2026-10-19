from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"

    database_url: str = "postgresql+psycopg://relayflow:localdev@127.0.0.1:5432/relayflow"
    redis_url: str = "redis://127.0.0.1:6379/0"

    admin_api_key: str = "development-only-change-me"
    fernet_key: str | None = None
    webhook_signing_secret: str | None = None

    telegram_bot_token: str | None = None
    telegram_allowed_user_ids: str = ""


settings = Settings()
