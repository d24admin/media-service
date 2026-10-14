from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class UploadRequest(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        if any(not 1 <= len(tag) <= 32 or not tag.isalnum() for tag in tags):
            raise ValueError("tags must be 1-32 alphanumeric characters")
        if len(set(tags)) != len(tags):
            raise ValueError("tags must be unique")
        return tags


class ImageItem(BaseModel):
    image_id: str
    status: str = "available"
    original_filename: str
    content_type: str
    size_bytes: int
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime


class UploadResponse(BaseModel):
    image_id: str
    status: str = "pending"
    upload: dict[str, Any]


class ListResponse(BaseModel):
    items: list[ImageItem]
    next_token: str | None = None
    page: int
    page_size: int
    total_items: int
    total_pages: int


class AccessResponse(BaseModel):
    image_id: str
    mode: str
    access_url: str
    expires_at: datetime
