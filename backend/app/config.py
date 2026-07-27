from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # local docker-compose defaults (non-standard host ports to avoid clashes)
    database_url: str = "postgresql+asyncpg://atelier:atelier@localhost:5433/atelier"
    redis_url: str = "redis://localhost:6380/0"

    jwt_secret: str = "dev-secret-change-in-prod"
    jwt_ttl_days: int = 30
    magic_link_ttl_seconds: int = 15 * 60

    # R2 in prod; MinIO locally. S3-compatible either way.
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "atelier"
    s3_secret_key: str = "atelier-local"
    s3_bucket: str = "atelier"

    # email delivery; when unset, magic links are printed to the API log
    resend_api_key: str | None = None
    mail_from: str = "Atelier <login@atelier.local>"
    app_base_url: str = "http://localhost:5173"

    # V1 single-tenant seed (TDD §2.1)
    workspace_id: str = "00000000-0000-0000-0000-000000000001"

    # public share-link router (TDD §4: "Rate limited to 60 req/min per IP")
    share_rate_limit_per_min: int = 60

    # model providers (Wada). Worker-only per TDD §1.2; unset -> segmentation
    # tasks no-op with a logged skip instead of crashing.
    gemini_api_key: str | None = None
    fal_api_key: str | None = None

    # PhotoRoom background removal (worker-only, like the model keys). With
    # photoroom_sandbox=True the key is used in sandbox mode (free tier,
    # watermarked output — dev only; prod flips the flag to spend Basic-tier
    # credits, $0.02/img).
    photoroom_api_key: str | None = None
    photoroom_sandbox: bool = True

    sentry_dsn: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
