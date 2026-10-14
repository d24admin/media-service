#!/usr/bin/env python3
"""Seed repeatable available images for local demos and pagination checks."""

import argparse
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import boto3

SEED_IMAGE_DIR = Path(__file__).with_name("seed-images")
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", nargs="+", default=["alice", "bob", "charlie"], help="owners to seed")
    parser.add_argument("--images-per-user", type=int, default=25, help="available images for each owner")
    parser.add_argument("--bucket", default=os.getenv("MEDIA_BUCKET_NAME", "media-service-local"))
    parser.add_argument("--table", default=os.getenv("MEDIA_TABLE_NAME", "media-service-local"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--endpoint-url",
        default=os.getenv("AWS_ENDPOINT_URL", "http://localhost.localstack.cloud:4566"),
    )
    return parser.parse_args()


def load_images() -> list[dict[str, Any]]:
    images = []
    for path in sorted(SEED_IMAGE_DIR.iterdir()):
        content_type = CONTENT_TYPES.get(path.suffix.lower())
        if content_type:
            images.append({"name": path.stem, "filename": path.name, "content_type": content_type, "body": path.read_bytes()})
    if not images:
        raise SystemExit(f"No seed images found in {SEED_IMAGE_DIR}")
    return images


def build_item(owner_id: str, index: int, created_at: datetime, image: dict[str, Any]) -> dict[str, object]:
    media_id = uuid5(NAMESPACE_URL, f"media-service-seed:{owner_id}:{index}").hex
    filename = f"demo-{image['name']}-{index:02d}{Path(str(image['filename'])).suffix}"
    tags = ["seed", "pagination", str(image["name"]), "travel" if index % 2 == 0 else "work"]
    return {
        "media_id": media_id,
        "owner_id": owner_id,
        "status": "AVAILABLE",
        "original_filename": filename,
        "declared_content_type": image["content_type"],
        "declared_size_bytes": len(image["body"]),
        "verified_content_type": image["content_type"],
        "verified_size_bytes": len(image["body"]),
        "title": f"{owner_id.title()} demo image {index:02d}",
        "tags": tags,
        "created_at": created_at.isoformat(),
    }


def main() -> int:
    args = parse_args()
    if args.images_per_user < 1:
        raise SystemExit("--images-per-user must be at least 1")

    credentials = {
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    }
    s3 = boto3.client("s3", region_name=args.region, endpoint_url=args.endpoint_url, **credentials)
    table = boto3.resource(
        "dynamodb",
        region_name=args.region,
        endpoint_url=args.endpoint_url,
        **credentials,
    ).Table(args.table)

    images = load_images()
    now = datetime.now(UTC)
    for owner_id in args.users:
        for index in range(1, args.images_per_user + 1):
            image = images[(index - 1) % len(images)]
            item = build_item(owner_id, index, now - timedelta(minutes=index), image)
            s3.put_object(
                Bucket=args.bucket,
                Key=f"images/{item['media_id']}",
                Body=image["body"],
                ContentType=image["content_type"],
            )
            table.put_item(Item=item)
        print(f"seeded {args.images_per_user} images for {owner_id}")

    print(f"seeded {len(args.users) * args.images_per_user} total images in {args.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
