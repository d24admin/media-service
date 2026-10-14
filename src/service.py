import logging
from datetime import UTC, datetime
from math import ceil
from uuid import uuid4

from config import Settings
from errors import MediaError, not_found
from models import AccessResponse, ImageItem, ListResponse, UploadRequest, UploadResponse

logger = logging.getLogger(__name__)


class MediaService:
    def __init__(self, repository, storage, settings: Settings) -> None:
        self.repository = repository
        self.storage = storage
        self.settings = settings

    def initiate(self, owner_id: str, request: UploadRequest) -> UploadResponse:
        self._validate_upload(request)
        media_id = uuid4().hex
        item = {
            "media_id": media_id,
            "owner_id": owner_id,
            "status": "PENDING",
            "original_filename": request.original_filename,
            "declared_content_type": request.content_type,
            "declared_size_bytes": request.size_bytes,
            "title": request.title,
            "tags": request.tags,
        }
        try:
            self.repository.create_pending(item)
            upload = self.storage.create_upload(
                self._object_key(media_id),
                request.content_type,
                self.settings.max_image_bytes,
                self.settings.upload_url_ttl_seconds,
            )
        except Exception as exc:
            logger.exception("could not create upload for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not create upload", 503) from exc
        logger.info(
            "upload_initiated media_id=%s owner_id=%s content_type=%s size_bytes=%s",
            media_id,
            owner_id,
            request.content_type,
            request.size_bytes,
        )
        return UploadResponse(image_id=media_id, upload=upload)

    def complete(self, owner_id: str, media_id: str) -> ImageItem:
        item = self._owned_item(owner_id, media_id)
        if item.get("status") == "AVAILABLE":
            logger.info("upload_completion_replayed media_id=%s owner_id=%s", media_id, owner_id)
            return self._image(item)

        verified = self._verify_upload(item, media_id)
        fields = {
            "verified_content_type": verified.content_type,
            "verified_size_bytes": verified.size_bytes,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            updated = self.repository.complete(media_id, fields)
        except Exception as exc:
            logger.exception("could not complete upload for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not complete image", 503) from exc

        if not updated:
            current = self._get_item(media_id)
            if current and current.get("status") == "AVAILABLE":
                logger.info("upload_completion_race_resolved media_id=%s owner_id=%s", media_id, owner_id)
                return self._image(current)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not complete image", 503)

        logger.info(
            "upload_completed media_id=%s owner_id=%s content_type=%s size_bytes=%s",
            media_id,
            owner_id,
            fields["verified_content_type"],
            fields["verified_size_bytes"],
        )
        return self._image({**item, **fields, "status": "AVAILABLE"})

    def list_images(
        self,
        owner_id: str,
        page_size: int,
        tag: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        next_token: str | None,
    ) -> ListResponse:
        if page_size < 1 or page_size > self.settings.max_page_size:
            raise MediaError("VALIDATION_ERROR", "page_size is out of range", 400)
        try:
            items, token, total_items, page = self.repository.list(
                owner_id, page_size, tag, created_from, created_to, next_token
            )
        except (ValueError, TypeError) as exc:
            raise MediaError("VALIDATION_ERROR", "next_token is invalid", 400) from exc
        except Exception as exc:
            logger.exception("could not list images for owner_id=%s", owner_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not list images", 503) from exc
        available = [item for item in items if item.get("status") == "AVAILABLE"]
        total_pages = ceil(total_items / page_size) if total_items else 0
        if page > max(total_pages, 1):
            raise MediaError("VALIDATION_ERROR", "next_token points past the last page", 400)
        logger.info(
            "images_listed owner_id=%s page=%s page_size=%s returned_items=%s total_items=%s total_pages=%s",
            owner_id,
            page,
            page_size,
            len(available),
            total_items,
            total_pages,
        )
        return ListResponse(
            items=[self._image(item) for item in available],
            next_token=token,
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=total_pages,
        )

    def access(self, owner_id: str, media_id: str, mode: str) -> AccessResponse:
        if mode not in {"preview", "download"}:
            raise MediaError("VALIDATION_ERROR", "mode must be preview or download", 400)
        item = self._owned_item(owner_id, media_id)
        if item.get("status") != "AVAILABLE":
            raise not_found()

        expires_at = datetime.now(UTC).timestamp() + self.settings.access_url_ttl_seconds
        try:
            url = self.storage.create_access_url(
                self._object_key(media_id),
                item["verified_content_type"],
                item["original_filename"],
                mode,
                self.settings.access_url_ttl_seconds,
            )
        except Exception as exc:
            logger.exception("could not create access URL for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not create access URL", 503) from exc
        logger.info("image_access_url_created media_id=%s owner_id=%s mode=%s", media_id, owner_id, mode)
        return AccessResponse(
            image_id=media_id,
            mode=mode,
            access_url=url,
            expires_at=datetime.fromtimestamp(expires_at, UTC),
        )

    def delete(self, owner_id: str, media_id: str) -> None:
        item = self._owned_item(owner_id, media_id)
        if item.get("status") != "AVAILABLE":
            raise not_found()
        try:
            self.storage.delete(self._object_key(media_id))
            if not self.repository.delete(media_id):
                raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not delete image metadata", 503)
        except MediaError:
            raise
        except Exception as exc:
            logger.exception("could not delete image for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not delete image", 503) from exc
        logger.info("image_deleted media_id=%s owner_id=%s", media_id, owner_id)

    def _validate_upload(self, request: UploadRequest) -> None:
        if request.content_type not in self.settings.allowed_types:
            raise MediaError("UNSUPPORTED_MEDIA_TYPE", "Unsupported image type", 415)
        if request.size_bytes > self.settings.max_image_bytes:
            raise MediaError("IMAGE_TOO_LARGE", "Image is too large", 413)

    def _verify_upload(self, item: dict, media_id: str):
        try:
            verified = self.storage.verify_object(
                self._object_key(media_id),
                self.settings.allowed_types,
                self.settings.max_image_bytes,
            )
        except FileNotFoundError as exc:
            raise MediaError("INVALID_IMAGE_STATE", "Uploaded image is not available", 409) from exc
        except ValueError as exc:
            raise MediaError("UNSUPPORTED_MEDIA_TYPE", str(exc), 415) from exc
        except Exception as exc:
            logger.exception("could not verify upload for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not verify image", 503) from exc

        if verified.content_type != item["declared_content_type"]:
            raise MediaError("UNSUPPORTED_MEDIA_TYPE", "Verified type differs from declared type", 415)
        if verified.size_bytes != item["declared_size_bytes"]:
            raise MediaError("VALIDATION_ERROR", "Verified size differs from declared size", 400)
        return verified

    def _owned_item(self, owner_id: str, media_id: str) -> dict:
        item = self._get_item(media_id)
        if not item or item.get("owner_id") != owner_id:
            raise not_found()
        return item

    def _get_item(self, media_id: str) -> dict | None:
        try:
            return self.repository.get(media_id)
        except Exception as exc:
            logger.exception("could not read image metadata for media_id=%s", media_id)
            raise MediaError("DEPENDENCY_UNAVAILABLE", "Could not read image metadata", 503) from exc

    @staticmethod
    def _object_key(media_id: str) -> str:
        return f"images/{media_id}"

    @staticmethod
    def _image(item: dict) -> ImageItem:
        created_at = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        return ImageItem(
            image_id=item["media_id"],
            original_filename=item["original_filename"],
            content_type=item.get("verified_content_type") or item["declared_content_type"],
            size_bytes=item.get("verified_size_bytes") or item["declared_size_bytes"],
            title=item.get("title"),
            tags=item.get("tags", []),
            created_at=created_at,
        )
