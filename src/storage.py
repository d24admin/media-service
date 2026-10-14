from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import boto3
from botocore.exceptions import ClientError
from PIL import Image


@dataclass(frozen=True)
class VerifiedObject:
    content_type: str
    size_bytes: int


class S3Storage:
    def __init__(
        self,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        public_endpoint_url: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.client = self._create_client(region, endpoint_url, access_key_id, secret_access_key)
        self.signing_client = self.client
        if public_endpoint_url and public_endpoint_url != endpoint_url:
            self.signing_client = self._create_client(region, public_endpoint_url, access_key_id, secret_access_key)

    def create_upload(self, key: str, content_type: str, max_size: int, ttl: int) -> dict[str, Any]:
        fields = {"key": key, "Content-Type": content_type}
        conditions = [["content-length-range", 1, max_size], {"Content-Type": content_type}, {"key": key}]
        signed = self.signing_client.generate_presigned_post(self.bucket, key, Fields=fields, Conditions=conditions, ExpiresIn=ttl)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        return {**signed, "expires_at": expires_at.isoformat()}

    def verify_object(self, key: str, allowed_types: set[str], max_size: int) -> VerifiedObject:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion", "404"}:
                raise FileNotFoundError(key) from exc
            raise
        content = response["Body"].read(max_size + 1)
        if not content or len(content) > max_size:
            raise ValueError("invalid image size")
        try:
            with Image.open(BytesIO(content)) as image:
                format_name = image.format
                if not format_name:
                    raise ValueError("missing image format")
                actual_type = Image.MIME.get(format_name)
                image.verify()
        except Exception as exc:
            raise ValueError("invalid image content") from exc
        if not actual_type or actual_type not in allowed_types:
            raise ValueError("unsupported image content")
        return VerifiedObject(actual_type, len(content))

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def create_access_url(self, key: str, content_type: str, filename: str, mode: str, ttl: int) -> str:
        safe_name = "".join(character for character in filename if character not in {'"', "\r", "\n"})
        disposition = "inline" if mode == "preview" else f'attachment; filename="{safe_name}"'
        return self.signing_client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key, "ResponseContentType": content_type, "ResponseContentDisposition": disposition}, ExpiresIn=ttl)

    @staticmethod
    def _create_client(region: str, endpoint_url: str | None, access_key_id: str | None, secret_access_key: str | None):
        return boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
