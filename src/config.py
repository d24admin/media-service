from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_endpoint_url: str | None = None
    aws_public_endpoint_url: str | None = None
    media_bucket_name: str = "media-service-local"
    media_table_name: str = "media-service-local"
    allowed_image_types: str = "image/jpeg,image/png"
    max_image_bytes: int = 10 * 1024 * 1024
    upload_url_ttl_seconds: int = 900
    access_url_ttl_seconds: int = 300
    default_page_size: int = 20
    max_page_size: int = 100

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def allowed_types(self) -> set[str]:
        return {item.strip() for item in self.allowed_image_types.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
