# Media Service

Media Service is a small, multi-user image management API for storing, searching, and serving image files. It supports the complete image lifecycle:

- create an upload request with image metadata;
- upload image bytes directly to S3-compatible storage;
- verify the uploaded object before publishing it;
- list and search the authenticated user’s available images;
- generate preview and download URLs; and
- delete an image and its metadata.

The service is implemented with FastAPI, DynamoDB, and S3. Local development uses LocalStack, while the same AWS SDK integrations can be configured for AWS services through environment variables.

## Architecture

```text
Client
  │
  ├── FastAPI locally / API Gateway HTTP API + Lambda
  │       │
  │       ├── DynamoDB: image metadata, ownership, and listing index
  │       └── S3: image objects
  │
  └── S3 direct upload/download using short-lived signed URLs
```

`X-User-Id` is used as the local development identity boundary. Every read, upload completion, preview, download, and delete operation checks the supplied user against the stored owner. In a production deployment, this value would come from a validated identity token rather than a client-provided header.

The component choices, request flow, data model, concurrency controls, and trade-offs are described in the [technical design](docs/design.md).

## Upload lifecycle

Uploads use two API calls:

1. `POST /v1/images/uploads` validates the declared metadata, creates a `PENDING` DynamoDB record, and returns a presigned S3 POST form.
2. The client sends the file directly to S3 using the returned form fields.
3. `POST /v1/images/{image_id}/complete` reads the object, validates its actual image bytes and size, compares them with the declaration, and conditionally changes the record to `AVAILABLE`.

Pending records are excluded from listing and access operations. Completion is idempotent, and the object key is derived consistently as `images/{image_id}`.

## API reference

All endpoints require the `X-User-Id` header. Error responses use this structure:

```json
{
  "error": {
    "code": "IMAGE_NOT_FOUND",
    "message": "Image not found"
  }
}
```

### Initiate an upload

```http
POST /v1/images/uploads
X-User-Id: user-123
Content-Type: application/json
```

```json
{
  "original_filename": "sunset.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 2457600,
  "title": "Evening sunset",
  "tags": ["travel", "sunset"]
}
```

`201 Created` returns the upload form. The client must send every value in `upload.fields` unchanged together with the file.

```json
{
  "image_id": "abc123",
  "status": "pending",
  "upload": {
    "url": "http://localhost:4566/media-service-local",
    "fields": {
      "key": "images/abc123",
      "Content-Type": "image/jpeg",
      "policy": "<opaque>",
      "x-amz-signature": "<opaque>"
    },
    "expires_at": "2026-10-14T12:00:00Z"
  }
}
```

### Complete an upload

```http
POST /v1/images/abc123/complete
X-User-Id: user-123
```

The service verifies the object’s actual content type and size before making it available.

```json
{
  "image_id": "abc123",
  "status": "available",
  "original_filename": "sunset.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 2457600,
  "title": "Evening sunset",
  "tags": ["travel", "sunset"],
  "created_at": "2026-10-14T11:55:00Z"
}
```

Calling completion again after a successful completion returns the same available image.

### List and filter images

```http
GET /v1/images?tag=sunset&created_from=2026-10-01T00:00:00Z&created_to=2026-10-31T23:59:59Z&page_size=20
X-User-Id: user-123
```

Supported query parameters are:

| Parameter | Behavior |
| --- | --- |
| `tag` | Exact tag match |
| `created_from` | Inclusive creation-time lower bound |
| `created_to` | Inclusive creation-time upper bound |
| `page_size` | Number of items per page, from 1 to 100 |
| `next_token` | Opaque cursor returned by the previous response |

Filters use AND semantics. Results are newest first and contain only available images.

```json
{
  "items": [
    {
      "image_id": "abc123",
      "status": "available",
      "original_filename": "sunset.jpg",
      "content_type": "image/jpeg",
      "size_bytes": 2457600,
      "title": "Evening sunset",
      "tags": ["travel", "sunset"],
      "created_at": "2026-10-14T11:55:00Z"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total_items": 1,
  "total_pages": 1,
  "next_token": null
}
```

`page` is one-based. `total_pages` is calculated from the matching available images and the requested page size. The repository continues querying through DynamoDB filter gaps so intermediate pages are filled when matching records are available. The final page always returns `next_token: null`.

### Preview or download an image

```http
GET /v1/images/abc123?mode=preview
X-User-Id: user-123
```

`mode` accepts `preview` or `download` and defaults to `preview`. Both modes return a short-lived signed S3 URL.

```json
{
  "image_id": "abc123",
  "mode": "preview",
  "access_url": "http://localhost:4566/media-service-local/images/abc123?<signed-query>",
  "expires_at": "2026-10-14T12:05:00Z"
}
```

Preview requests use inline content disposition. Download requests use an attachment disposition with the original filename.

### Delete an image

```http
DELETE /v1/images/abc123
X-User-Id: user-123
```

`204 No Content` confirms deletion of both the S3 object and DynamoDB record. A repeated delete, or a request from another owner, returns the same `404 Not Found` response used for an unknown image.

Common response statuses are `400` for invalid input or cursors, `404` for unknown or unowned images, `409` when an upload object is not ready, `413` for an oversized image, `415` for unsupported or mismatched content, and `503` for a temporary storage or database failure.

## Data model

DynamoDB stores the fields required by the API and lifecycle:

```text
media_id
owner_id
status
original_filename
declared_content_type
declared_size_bytes
verified_content_type
verified_size_bytes
title
tags
created_at
```

`media_id` is the table key. The `owner-created-index` GSI uses `owner_id` as its partition key and `created_at` as its sort key, which supports owner isolation and newest-first listing. `created_at` is written only when completion succeeds, so pending records do not appear in the listing index.

`original_filename` preserves the source file name and controls download naming. `title` is an optional user-facing label, so it can evolve independently from the uploaded file name.

The implementation does not use S3 versioning, staging keys, final-object copies, or a publish-copy step. The object key is derived as `images/{media_id}`. Actual image content is inspected during completion rather than trusting the filename or declared content type.

## Local setup

Prerequisites:

- Docker Desktop with Docker Compose;
- Python 3.11 or newer; and
- a LocalStack auth token.

Set the token in the shell used to start the service:

```bash
export LOCALSTACK_AUTH_TOKEN="your-token"
make local
```

`make local` installs development dependencies when required, starts the current LocalStack image, builds the Lambda package, provisions S3, DynamoDB, IAM, Lambda, and API Gateway HTTP API resources, and prints the browser URL for the API Gateway/Lambda path. Provisioning is idempotent. No AWS account or production credentials are required for this local environment.

Open the printed API Gateway URL in a browser. The bundled console is served by Lambda and its relative API requests therefore use API Gateway as well. It supports upload, metadata entry, filtering, pagination, preview, download, and deletion.

### Local FastAPI versus LocalStack Lambda

The project supports both paths:

- `make local` uses LocalStack API Gateway and invokes the packaged `media-service-local` Lambda on demand. Lambda is not a continuously running server, and its CloudWatch log group is created only after the function is invoked.
- `make local-fastapi` starts the local Uvicorn process at `http://127.0.0.1:8000` for application-level debugging. Requests made there do not run on Lambda.

Application logs include the request method, path, status, duration, owner context, media ID, pagination totals, and upload lifecycle events. Signed URLs, request query strings, image bytes, and request bodies are intentionally excluded.


To create repeatable demo data for multiple owners and pagination:

```bash
python scripts/seed-local.py --users alice bob --images-per-user 25
```

To remove the LocalStack containers and persisted data:

```bash
docker compose down -v
```

## Demo walkthrough

The following sequence demonstrates the required behavior:

1. Upload a JPEG or PNG with a title and tags as `alice`.
2. Complete the upload and confirm it appears in Alice’s list.
3. Apply a tag filter and a creation-time range.
4. Seed or create enough records to navigate multiple pages and verify the `total_pages` and final-page cursor behavior.
5. Switch to `bob` and confirm Alice’s image is neither listed nor accessible.
6. Upload the same filename for both users and confirm the generated media IDs remain independent.
7. Preview, download, delete, and repeat the delete request to verify idempotent failure behavior.

## Repository layout

```text
src/
├── config.py       # environment-backed settings
├── errors.py       # application error envelope
├── handler.py      # Lambda entrypoint
├── main.py         # FastAPI application and local console
├── models.py       # request and response models
├── repository.py   # DynamoDB access and pagination cursors
├── routes.py       # HTTP routes
├── service.py      # lifecycle and ownership rules
├── storage.py      # S3 signed URLs and object verification
└── static/index.html
scripts/
├── provision-local.sh
├── seed-local.py
├── start-local.sh
└── seed-images/       # small real JPEG fixtures used by the local seeder
```

## Scope and limitations

The implementation intentionally excludes a production identity provider, background cleanup for abandoned pending uploads, thumbnails, malware scanning, and deployment automation. The LocalStack provisioner is a repeatable local environment; production deployment would supply AWS endpoints and credentials through the deployment platform’s secret manager and use managed AWS resources.
