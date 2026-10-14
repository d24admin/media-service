import base64
import binascii
import json
from datetime import datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key


class MediaRepository:
    def __init__(
        self,
        table_name: str,
        region: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        self.table = dynamodb.Table(table_name)

    def create_pending(self, item: dict[str, Any]) -> None:
        self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(media_id)")

    def get(self, media_id: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"media_id": media_id}, ConsistentRead=True).get("Item")

    def complete(self, media_id: str, fields: dict[str, Any]) -> bool:
        values = {
            ":pending": "PENDING",
            ":available": "AVAILABLE",
            ":content_type": fields["verified_content_type"],
            ":size": fields["verified_size_bytes"],
            ":created_at": fields["created_at"],
        }
        try:
            self.table.update_item(
                Key={"media_id": media_id},
                UpdateExpression=(
                    "SET verified_content_type = :content_type, "
                    "verified_size_bytes = :size, created_at = :created_at, #status = :available"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
                ConditionExpression="#status = :pending",
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def list(
        self,
        owner_id: str,
        page_size: int,
        tag: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        token: str | None,
    ) -> tuple[list[dict[str, Any]], str | None, int, int]:
        cursor = self._decode_cursor(token) if token else None
        page = cursor["page"] if cursor else 1
        start_key = cursor["key"] if cursor else None
        items: list[dict[str, Any]] = []
        last_key = start_key

        while len(items) < page_size:
            response = self.table.query(
                **self._query_args(
                    owner_id,
                    tag,
                    created_from,
                    created_to,
                    last_key,
                    page_size - len(items),
                )
            )
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break

        total_items = self._count(owner_id, tag, created_from, created_to)
        next_token = self._encode_cursor(last_key, page + 1)
        return items, next_token, total_items, page

    def delete(self, media_id: str) -> bool:
        try:
            self.table.delete_item(Key={"media_id": media_id}, ConditionExpression="attribute_exists(media_id)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def _query_args(
        self,
        owner_id: str,
        tag: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        start_key: dict[str, Any] | None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        condition = Key("owner_id").eq(owner_id)
        if created_from and created_to:
            condition &= Key("created_at").between(created_from.isoformat(), created_to.isoformat())
        elif created_from:
            condition &= Key("created_at").gte(created_from.isoformat())
        elif created_to:
            condition &= Key("created_at").lte(created_to.isoformat())

        args: dict[str, Any] = {
            "IndexName": "owner-created-index",
            "KeyConditionExpression": condition,
            "ScanIndexForward": False,
        }
        filters = [Attr("status").eq("AVAILABLE")]
        if tag:
            filters.append(Attr("tags").contains(tag))
        args["FilterExpression"] = filters[0] if len(filters) == 1 else filters[0] & filters[1]
        if page_size is not None:
            args["Limit"] = page_size
        if start_key:
            args["ExclusiveStartKey"] = start_key
        return args

    @staticmethod
    def _encode_cursor(last_key: dict[str, Any] | None, page: int) -> str | None:
        if not last_key:
            return None
        encoded = json.dumps({"key": last_key, "page": page}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).decode()

    @staticmethod
    def _decode_cursor(token: str) -> dict[str, Any]:
        try:
            decoded = base64.urlsafe_b64decode(token.encode()).decode()
            value = json.loads(decoded)
        except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("next_token is invalid") from exc
        if not isinstance(value, dict) or not isinstance(value.get("key"), dict):
            raise TypeError("next_token is invalid")
        page = value.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 2:
            raise ValueError("next_token is invalid")
        return {"key": value["key"], "page": page}

    def _count(
        self,
        owner_id: str,
        tag: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> int:
        total = 0
        start_key = None
        while True:
            args = self._query_args(owner_id, tag, created_from, created_to, start_key)
            args["Select"] = "COUNT"
            response = self.table.query(**args)
            total += int(response.get("Count", 0))
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return total
