from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/webhooks"
    db_pool_size: int = Field(default=10, ge=1)

    log_level: str = "INFO"
    log_json: bool = True

    # hosts/CIDRs that skip the SSRF check, dev and tests only
    ssrf_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    worker_batch_size: int = Field(default=50, ge=1)
    worker_concurrency: int = Field(default=20, ge=1)
    worker_poll_interval_s: float = Field(default=1.0, gt=0)
    worker_lease_s: float = Field(default=60.0, gt=0)
    worker_metrics_port: int = 9100
    worker_stats_interval_s: float = Field(default=5.0, gt=0)
    worker_shutdown_grace_s: float = Field(default=30.0, gt=0)

    delivery_max_attempts: int = Field(default=8, ge=1)
    backoff_base_s: float = Field(default=2.0, gt=0)
    backoff_max_s: float = Field(default=3600.0, gt=0)
    http_connect_timeout_s: float = Field(default=5.0, gt=0)
    http_total_timeout_s: float = Field(default=15.0, gt=0)
    max_response_bytes: int = Field(default=64 * 1024, ge=0)
    stored_response_bytes: int = Field(default=4096, ge=0)

    breaker_failure_threshold: int = Field(default=5, ge=1)
    breaker_cooldown_s: float = Field(default=300.0, gt=0)

    @model_validator(mode="after")
    def _lease_outlives_request(self) -> "Settings":
        # otherwise a slow but alive attempt loses its lease and gets sent twice
        if self.worker_lease_s <= self.http_total_timeout_s:
            raise ValueError("worker_lease_s must be greater than http_total_timeout_s")
        if self.http_connect_timeout_s > self.http_total_timeout_s:
            raise ValueError("http_connect_timeout_s must not exceed http_total_timeout_s")
        return self

    @field_validator("ssrf_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value
