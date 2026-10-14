from datetime import datetime
from functools import lru_cache

from fastapi import APIRouter, Header, Query, Response

from config import get_settings
from errors import MediaError
from models import UploadRequest
from repository import MediaRepository
from service import MediaService
from storage import S3Storage


@lru_cache
def build_service() -> MediaService:
    settings = get_settings()
    repository = MediaRepository(settings.media_table_name, settings.aws_region, settings.aws_endpoint_url, settings.aws_access_key_id, settings.aws_secret_access_key)
    storage = S3Storage(
        settings.media_bucket_name,
        settings.aws_region,
        settings.aws_endpoint_url,
        settings.aws_access_key_id,
        settings.aws_secret_access_key,
        settings.aws_public_endpoint_url,
    )
    return MediaService(repository, storage, settings)


router = APIRouter(prefix="/v1/images")


def require_owner(value: str | None) -> str:
    if not value:
        raise MediaError("VALIDATION_ERROR", "X-User-Id header is required", 400)
    return value


@router.post("/uploads", status_code=201)
def initiate(body: UploadRequest, x_user_id: str | None = Header(default=None)):
    return build_service().initiate(require_owner(x_user_id), body)


@router.post("/{media_id}/complete")
def complete(media_id: str, x_user_id: str | None = Header(default=None)):
    return build_service().complete(require_owner(x_user_id), media_id)


@router.get("")
def list_images(x_user_id: str | None = Header(default=None), tag: str | None = None, created_from: datetime | None = None, created_to: datetime | None = None, page_size: int = Query(default=20), next_token: str | None = None):
    if created_from and created_to and created_from > created_to:
        raise MediaError("VALIDATION_ERROR", "created_from must be before created_to", 400)
    return build_service().list_images(require_owner(x_user_id), page_size, tag, created_from, created_to, next_token)


@router.get("/{media_id}")
def access(media_id: str, mode: str = "preview", x_user_id: str | None = Header(default=None)):
    return build_service().access(require_owner(x_user_id), media_id, mode)


@router.delete("/{media_id}", status_code=204)
def delete(media_id: str, x_user_id: str | None = Header(default=None)):
    build_service().delete(require_owner(x_user_id), media_id)
    return Response(status_code=204)
