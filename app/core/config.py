from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="RM_",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="dev", description="dev | prod | test")
    debug: bool = False
    api_v1_prefix: str = "/v1"
    public_base_url: str = "http://localhost:8000"

    secret_key: SecretStr = SecretStr("change-me-in-production-please-32bytes!")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 60
    id_token_ttl_seconds: int = 60 * 60
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30

    database_url: PostgresDsn = Field(
        default="postgresql+asyncpg://rainmaker:rainmaker@localhost:5432/rainmaker"
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")

    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = SecretStr("rainmaker")
    minio_secret_key: SecretStr = SecretStr("rainmakersecret")
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_bucket_ota: str = "rainmaker-ota"
    minio_bucket_files: str = "rainmaker-files"

    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 8883
    mqtt_ws_host: str = "localhost"
    mqtt_ws_port: int = 8083
    mqtt_internal_user: str = "rainmaker-backend"
    mqtt_internal_password: SecretStr = SecretStr("rainmaker-backend-pw")

    pki_dir: Path = Path("./var/pki")
    pki_country: str = "FR"
    pki_org: str = "ESP RainMaker Self-Hosted"
    pki_ca_cn: str = "ESP RainMaker Root CA"
    cert_validity_days: int = 365 * 5

    email_from: str = "no-reply@rainmaker.local"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False

    feature_mqtt_enabled: bool = True
    feature_oauth_enabled: bool = False
    feature_push_enabled: bool = False
    feature_video_assume_role: bool = False

    log_level: str = "INFO"
    log_json: bool = True

    cors_allow_origins: list[str] = ["*"]

    @property
    def sync_database_url(self) -> str:
        return str(self.database_url).replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
