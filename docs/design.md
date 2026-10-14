# Media Service design

Media Service stores image metadata in DynamoDB and image bytes in S3. It exposes four flows: upload, list/filter, view/download, and delete.

## Architecture

```text
Client ──► API Gateway HTTP API ──► Lambda/FastAPI
                                      ├── DynamoDB metadata
                                      └── S3 image objects
Client ─────────────────────────────► S3 using signed URLs
```

| Component | Why it is used |
| --- | --- |
| API Gateway HTTP API | Stable HTTP endpoint and request routing. |
| Lambda | Stateless execution that can scale across concurrent requests. |
| S3 | Durable object storage and direct upload/download through signed URLs. |
| DynamoDB | Owner-scoped metadata queries and conditional state changes. |
| FastAPI | Small, testable HTTP layer used locally and in Lambda. |

`make local` runs the same path with LocalStack. `make local-fastapi` runs FastAPI directly with Uvicorn.

LocalStack uses HTTP on port `4566` because it does not need a trusted TLS certificate for local development. A real AWS deployment would use the HTTPS endpoints provided by API Gateway and S3.

## Shared rules

- Every request requires `X-User-Id`, which is a development identity stub rather than authentication.
- Ownership is checked in the service layer; unknown and unowned images return the same `404`.
- Only `AVAILABLE` images are listable, viewable, downloadable, or deletable.
- The S3 key is derived as `images/{media_id}` and is not supplied by the client.
- DynamoDB and S3 are the source of truth; no request depends on in-memory state.

## 1. Upload image

1. `POST /v1/images/uploads` validates filename, type, size, title, and tags.
2. The service generates a unique ID, writes a conditional `PENDING` DynamoDB record, and returns a presigned S3 POST for `images/{media_id}`.
3. The client uploads bytes directly to S3.
4. `POST /v1/images/{media_id}/complete` verifies ownership, reads the object, validates its actual image content with Pillow, compares verified type/size with the declaration, and conditionally changes the record to `AVAILABLE`.

`created_at` and verified fields are written only on successful completion. Completion is idempotent; concurrent completion requests publish the image once.

| Edge case | Behavior |
| --- | --- |
| Invalid metadata, unsupported type, or oversized declaration | Client error before publishing an image. |
| Missing, empty, corrupt, or mismatched object | Completion fails; record remains unavailable. |
| Same filename from different users | Unique IDs and S3 keys prevent overwrites. |
| Wrong owner, repeated completion, or storage failure | `404`, existing available result, or `503` respectively. |

## 2. List, search, and filter

`GET /v1/images` queries the `owner-created-index` GSI in newest-first order. It supports exact `tag`, inclusive `created_from`, and inclusive `created_to` filters. Filters use AND semantics.

The response contains `page`, `page_size`, `total_items`, `total_pages`, and an opaque `next_token`. The repository advances the DynamoDB cursor through filtered records until the page is full or results end. The final page returns `next_token: null`.

| Edge case | Behavior |
| --- | --- |
| No matches or pending records | Empty result; pending records stay hidden. |
| Invalid date range or page size | `400` response. |
| Malformed or past-end cursor | `400` response. |
| Another user’s images | Not returned because the query is owner-partitioned. |
| DynamoDB failure | `503` response. |

Exact totals require a count query. For very large collections, cursor-only pagination or a separately maintained count may be preferable.

## 3. View or download

`GET /v1/images/{media_id}?mode=preview|download` reads metadata consistently, checks ownership and availability, and returns a short-lived signed S3 URL.

- `preview` uses inline content disposition.
- `download` uses attachment disposition with the original filename.
- Image bytes do not pass through Lambda.

| Edge case | Behavior |
| --- | --- |
| Unknown, unowned, pending, or deleted image | `404` response. |
| Unsupported mode | `400` response. |
| Signed URL generation failure | `503` response. |
| Expired URL | Client requests a new URL; metadata is unchanged. |

## 4. Delete image

`DELETE /v1/images/{media_id}` checks ownership and availability, deletes `images/{media_id}` from S3, and conditionally deletes the DynamoDB record. It returns `204 No Content` only after both operations succeed.

| Edge case | Behavior |
| --- | --- |
| Unknown, unowned, or pending image | `404` response. |
| Repeated delete | `404`; the resource remains absent. |
| S3 deletion failure | `503`; metadata remains available for retry. |
| Metadata deletion failure after S3 deletion | `503`; retry safely completes metadata cleanup. |
| Concurrent delete | Conditional DynamoDB delete allows only one successful deletion. |

## Data model

DynamoDB uses `media_id` as the primary key. The `owner-created-index` GSI uses `owner_id` as its partition key and `created_at` as its sort key.

| Attribute | Purpose |
| --- | --- |
| `media_id` | Unique image identifier and primary key. |
| `owner_id` | Ownership boundary for every operation. |
| `status` | `PENDING` or `AVAILABLE` lifecycle state. |
| `original_filename` | Display name and download filename. |
| `declared_content_type` | Type supplied by the client. |
| `declared_size_bytes` | Size supplied by the client. |
| `verified_content_type` | Type detected from actual image bytes. |
| `verified_size_bytes` | Size measured from the stored object. |
| `title` | Optional user-facing label. |
| `tags` | Optional exact-match filter values. |
| `created_at` | Completion time and GSI sort key. |

